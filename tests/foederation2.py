"""Foederation 2.3 (Protokoll 2): zwei eigene Saga-Instanzen A und B (eigene Datenbanken, eigene Ports) wie tests/foederation.py.

Geprueft: /.well-known/saga mit protocol 2 + Faehigkeiten, Aushandlung (hello, beide Seiten), Express-Zustellung (sofort,
Ausgangskorb leer) mit Rueckfall auf den Ausgangskorb (B kurz weg), send_now mit Frist, Reihenfolge je Kette (eine wartende
fruehere Zeile haelt die Kette an), idempotente Ereignisse (dup/stale/Luecke), Admin-Schalter je Instanz (Formular + JSON-API,
Ausnahme "nur gemeinsame Gruppen"), Adressbuch-Abgleich (pseudonyme Handles, nie Anmeldenamen/E-Mail/Gruppen, Schalter je
Konto, deaktivierte Konten, gleiche Namen unterscheidbar, Profilbilder, Suche in Chat/Teilen, Trennen loescht sofort),
Schluesselverzeichnis + Krypto v2 (Zertifikate hochladen/pruefen, fremde Schluessel signiert ueber den eigenen Server,
Zwischenspeicher, chat.keys.changed, Ende-zu-Ende mit den geholten Schluesseln, Sicherheitsnummer v2).

Kompatibilitaet: FED_B_APP=<Verzeichnis mit Saga 2.1/2.2> startet B aus diesem Stand -> Protokoll 1 laeuft weiter (Teilen,
Benachrichtigung), A erkennt Protokoll 1, keine Zusatzfunktionen, keine Serverfehler. Raeumt auf (Datenbanken geloescht)."""
import io, json, os, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foederation as T  # noqa: E402  (Helfer: HTTP, Admin, Signaturen, Instanzen)
import chatlib as C  # noqa: E402
from saga import chat_v2 as SV, fedsig  # noqa: E402

APP = T.APP
B_APP = os.environ.get("FED_B_APP") or APP
COMPAT = B_APP != APP
T.I["a"].update(port=8195, admin=8194, db="saga_f2a", url="http://127.0.0.1:8195", host="127.0.0.1:8195", state="/tmp/fed2test-a", allow="localhost:8197")
T.I["b"].update(port=8197, admin=8196, db="saga_f2b", url="http://localhost:8197", host="localhost:8197", state="/tmp/fed2test-b", allow="127.0.0.1:8195")
HA, HB = T.I["a"]["host"], T.I["b"]["host"]
ok, hreq, api, sql, wait, jd = T.ok, T.hreq, T.api, None, T.wait, T.jd
_env0 = T.env_of


def env_of(k):
    e = _env0(k)
    e["PYTHONPATH"] = B_APP if k == "b" else APP
    e["FED_EXPRESS_TIMEOUT"] = "3"
    return e


def py(k, code):
    import subprocess
    r = subprocess.run([sys.executable, "-W", "ignore", "-c", "import json\n" + code], env=env_of(k), cwd=B_APP if k == "b" else APP,
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        print(r.stdout[-2000:], r.stderr[-3000:], flush=True)
        raise RuntimeError("py fehlgeschlagen")
    lines = [x for x in r.stdout.strip().splitlines() if x.strip()]
    return json.loads(lines[-1]) if lines else None


def start(k):
    import subprocess
    i = T.I[k]
    os.makedirs(i["state"], exist_ok=True)
    log = open(os.path.join(i["state"], "server.log"), "a")
    T.procs[k] = subprocess.Popen([sys.executable, "run.py"], env=env_of(k), cwd=B_APP if k == "b" else APP, stdout=log, stderr=subprocess.STDOUT)
    ok(f"{k.upper()}: gestartet", wait(lambda: T._health(k), 40))


T.env_of, T.py, T.start = env_of, py, start
sql = T.sql


def png(w=64, h=64, color=(30, 120, 200)):
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "PNG")
    return b.getvalue()


def admin_json(adm, m, p, body=None):
    tok = adm.csrf()
    hdr = {"Cookie": "; ".join(f"{a}={b}" for a, b in adm.ck.items()), "X-CSRF-Token": tok}
    s, _, d = hreq(adm.k, m, p, body=body, hdr=hdr, admin=True)
    return s, jd(d)


def setting(k, key, val):
    py(k, f"import auth\nauth.save_setting({key!r}, {val!r})\nprint(json.dumps(True))")


def dirn(k, pid=None):
    return sql(k, "SELECT count(*) FROM fed_directory" + (" WHERE peer_id=?" if pid else ""), (pid,) if pid else ())[0][0]


def connect(A, B):
    _, html = A.post({"action": "invite"})
    code = re.search(r'Code: <code class="fed-fp">([A-Za-z0-9_-]{20,})</code>', html).group(1)
    fp_a = jd(hreq("a", "GET", "/.well-known/saga")[2])["fingerprint"]
    B.post({"action": "preview", "url": T.I["a"]["url"], "code": code})
    _, html = B.post({"action": "connect", "url": T.I["a"]["url"], "code": code, "fingerprint": fp_a})
    pa = sql("a", "SELECT id FROM fed_peer")[0][0]
    _, html = A.post({"action": "confirm", "id": str(pa)})
    ok(f"verbunden ({T.flash(html)[:40]})", "bestätigt" in T.flash(html))
    return pa, sql("b", "SELECT id FROM fed_peer")[0][0]


SETUP_B = T.SETUP


def main():
    for k in T.I:
        T.pg_admin(f"DROP DATABASE IF EXISTS {T.I[k]['db']} WITH (FORCE)")
        T.pg_admin(f"CREATE DATABASE {T.I[k]['db']}")
        for f in ("server.log",):
            try:
                os.remove(os.path.join(T.I[k]["state"], f))
            except OSError:
                pass
    py("a", T.SETUP.format(allow=T.I["a"]["allow"], users=[("fa-anna", "Anna Berg", "anna"), ("fa-carl", "Carl Ohne", None),
                                                            ("fa-lisa", "fa-lisa", None), ("fa-mia", "mia@example.org", None)],
                                    groups=[("fateam", ["fa-anna"])]))
    py("b", SETUP_B.format(allow=T.I["b"]["allow"], users=[("fb-bert", "Bert Gast", "bert"), ("fb-anna", "Anna Berg", None),
                                                          ("fb-anna2", "Anna Berg", "annab"), ("fb-olga", "Olga Aus", "olga"),
                                                          ("fb-dis", "Dieter Aus", "dieter")], groups=[("fbteam", ["fb-bert"])]))
    sql("b", "UPDATE users SET disabled=1, email='dieter@example.org' WHERE username='fb-dis'")
    sql("b", "UPDATE users SET email='bert@example.org' WHERE username='fb-bert'")
    sql("a", "UPDATE users SET email='Mia@Example.org' WHERE username='fa-mia'")      # SSO: Anzeigename = E-Mail-Adresse
    start("a"); start("b")
    A, B = T.Admin("a"), T.Admin("b")
    ok("Admin A und B angemeldet", A.login() and B.login())
    for adm in (A, B):
        adm.post({"action": "enable", "on": "1"})
    if not COMPAT:
        s, d = api("b", "fb-olga", "PUT", "/api/account/federation", {"directory": False})
        ok("B: Olga erscheint nicht in Adressbuechern (Schalter je Konto)", s == 200 and d["directory"] is False)
    wk = jd(hreq("a", "GET", "/.well-known/saga")[2])
    ok("well-known: protocol 2, protocols [1,2]", wk.get("protocol") == 2 and wk.get("protocols") == [1, 2])
    ok("well-known: Faehigkeiten v1 + v2", {"share", "read", "comment", "v2", "express", "directory", "keys", "handles"} <= set(wk.get("capabilities") or []))
    pa, pb = connect(A, B)
    return A, B, pa, pb


# =====================================================================================================
def part_compat(A, B, pa, pb):
    """A = 2.3, B = aeltere Version (Protokoll 1)."""
    ok("A: Gegenstelle Protokoll 1 erkannt (hello -> 404)", wait(lambda: sql("a", "SELECT protocol, caps, caps_at FROM fed_peer")[0][2] is not None, 20)
       and sql("a", "SELECT protocol, caps FROM fed_peer")[0][:2] == [1, "[]"])
    html = A.req("GET", "/federation")[2]
    ok("A: Admin zeigt 'Keine Zusatzfähigkeiten'", "Keine Zusatzfähigkeiten" in html)
    s, d = api("a", "fa-anna", "POST", "/api/notes", {"title": "Kompat-Seite", "body": T.blocks(T.P("Hallo B", "b1"))})
    nid = d["id"]
    s, d = api("a", "fa-anna", "PUT", f"/api/notes/{nid}/shares", {"type": "remote", "target": f"@bert@{HB}", "role": "comment"})
    ok(f"A -> B (Protokoll 1): Teilen an bert@B ({s})", s == 200)
    ok("B: Eingangs-Verweis da (Ausgangskorb v1)", wait(lambda: sql("b", "SELECT count(*) FROM fed_inbound")[0][0] == 1, 40))
    ok("A: Ausgangskorb leer", wait(lambda: sql("a", "SELECT count(*) FROM fed_outbox")[0][0] == 0, 20))
    s, d = api("b", "fb-bert", "GET", "/api/fed/shared")
    ok("B: 'Mit mir geteilt' zeigt die Seite von A", s == 200 and any(x["title"] == "Kompat-Seite" for x in d["items"]))
    rid = next(x["id"] for x in d["items"] if x["title"] == "Kompat-Seite")
    s, d = api("b", "fb-bert", "POST", f"/api/notes/{rid}/comments", {"block_id": "b1", "body": "Kommentar von B"})
    ok(f"B (alt) kommentiert Seite von A ({s})", s in (200, 201))
    ok("A: Benachrichtigung ueber den Kommentar von B", wait(lambda: sql("a", "SELECT count(*) FROM notification WHERE uid='fa-anna' AND kind='comment.new'")[0][0] >= 1, 30))
    s, d = api("b", "fb-bert", "POST", "/api/notes", {"title": "Seite von B (alt)", "body": T.blocks(T.P("von B"))})
    s, _ = api("b", "fb-bert", "PUT", f"/api/notes/{d['id']}/shares", {"type": "remote", "target": f"@anna@{HA}", "role": "edit"})
    ok(f"B (alt) -> A: Teilen an anna@A ({s})", s == 200)
    ok("A: Eingangs-Verweis von B (Ereignis Protokoll 1 an /fed/v1/events)", wait(lambda: sql("a", "SELECT count(*) FROM fed_inbound")[0][0] == 1, 40))
    s, d = api("a", "fa-anna", "GET", "/api/fed/shared")
    ok("A: liest Seite von B (alt)", s == 200 and any(x["title"] == "Seite von B (alt)" for x in d["items"]))
    s, d = api("a", "fa-anna", "GET", "/api/chat/people?q=bert")
    ok("A: keine Adressbuch-Treffer von B (alte Version)", s == 200 and d.get("remote") == [])
    s, d = api("a", "fa-anna", "GET", f"/api/chat/v2/keys?handles=~abcdefghijklmnop@{HB}")
    ok("A: Schluessel bei alter Gegenstelle -> null", s == 200 and d["keys"].get(f"~abcdefghijklmnop@{HB}") is None)
    s, d = api("a", "fa-anna", "GET", f"/api/share/targets?q=bert@{HB}")
    ok("A: genaue Adresse bert@B weiter auffindbar (WebFinger v1)", s == 200 and any(x["target"] == f"@bert@{HB}" for x in d.get("remote") or []))


# =====================================================================================================
def part_protocol(A, B, pa, pb):
    ok("Aushandlung: A kennt Faehigkeiten von B", wait(lambda: "directory" in json.loads(sql("a", "SELECT caps FROM fed_peer")[0][0]), 20))
    ok("Aushandlung: B kennt Faehigkeiten von A", wait(lambda: "directory" in json.loads(sql("b", "SELECT caps FROM fed_peer")[0][0]), 20))
    ok("beide Protokoll 2", sql("a", "SELECT protocol FROM fed_peer")[0][0] == 2 and sql("b", "SELECT protocol FROM fed_peer")[0][0] == 2)
    ok("Pruefprotokoll: Faehigkeiten der Gegenseite", sql("a", "SELECT count(*) FROM fed_log WHERE event='peer.capabilities'")[0][0] >= 1)
    st = admin_json(A, "GET", "/api/federation")
    ok("Admin-API: Stand mit Faehigkeiten und Schaltern", st[0] == 200 and st[1]["protocol"] == 2
       and st[1]["peers"][0]["features"]["directory"] and st[1]["peers"][0]["opts"]["scope_exception"] is False)
    ok("Admin-API ohne Anmeldung -> 401", hreq("a", "GET", "/api/federation", admin=True)[0] == 401)
    s, _ = admin_json(A, "PUT", f"/api/federation/peers/{pa}", {"calls": False})
    hdr = {"Cookie": "; ".join(f"{a}={b}" for a, b in A.ck.items())}
    ok("Admin-API PUT ohne CSRF -> 403", hreq("a", "PUT", f"/api/federation/peers/{pa}", body={"calls": True}, hdr=hdr, admin=True)[0] == 403)
    ok("Admin-API PUT: Anrufe aus", s == 200 and json.loads(sql("a", "SELECT opts FROM fed_peer")[0][0])["calls"] is False)
    admin_json(A, "PUT", f"/api/federation/peers/{pa}", {"calls": True})
    ok("Admin-Seite zeigt Funktionen je Instanz", "Funktionen je Instanz" in A.req("GET", "/federation")[2])

    # --- Express ----------------------------------------------------------------------------------------------
    s, d = api("a", "fa-anna", "POST", "/api/notes", {"title": "Express-Seite", "body": T.blocks(T.P("x"))})
    nid = d["id"]
    t0 = time.time()
    s, _ = api("a", "fa-anna", "PUT", f"/api/notes/{nid}/shares", {"type": "remote", "target": f"@bert@{HB}", "role": "view"})
    got = wait(lambda: sql("b", "SELECT count(*) FROM fed_inbound WHERE note_id=?", (nid,))[0][0] == 1, 10, 0.1)
    dt = time.time() - t0
    ok(f"Express: Freigabe sofort bei B ({dt:.1f} s, ohne 5-s-Takt des Arbeiters)", s == 200 and got and dt < 3.5)
    ok("Express: Ausgangskorb leer", wait(lambda: sql("a", "SELECT count(*) FROM fed_outbox")[0][0] == 0, 5, 0.2))
    r = py("a", f"from saga import federation as F\nprint(json.dumps(F.send_now({pa}, {{'type': 'test.noop'}}, chain='t1')))")
    ok("send_now: synchron bestaetigt", r is True)

    # --- Reihenfolge je Kette ---------------------------------------------------------------------------------
    r = py("a", f"""
import time
from db import connect
from saga import federation as F
with connect() as c, c:
    ids = [F._enqueue(c, {pa}, {{'type': 'test.noop', 'n': i}}, chain='t2', express=False) for i in range(3)]
    c.execute("UPDATE fed_outbox SET next_try=? WHERE id=?", (time.time() + 3600, ids[0]))
with connect() as c, c:
    blocked = [r[0] for r in F._pick_due(c, time.time()) if r[5] == 't2']
    c.rollback()
with connect() as c, c:
    c.execute("UPDATE fed_outbox SET next_try=0, locked_until=0 WHERE chain='t2'")
n = 0
for _ in range(5):
    n += F.deliver_due()
with connect() as c:
    left = c.execute("SELECT count(*) FROM fed_outbox WHERE chain='t2'").fetchone()[0]
print(json.dumps([blocked, n, left]))""")
    ok(f"Kette: wartende erste Zeile haelt die Kette an ({r[0]})", r[0] == [])
    ok(f"Kette: danach alle drei zugestellt ({r[1]}, Rest {r[2]})", r[1] >= 3 and r[2] == 0)
    ok("B: Kette t2 bis Nummer 3 verarbeitet, keine Luecke", sql("b", "SELECT seq FROM fed_chain_in WHERE chain='t2'") == [[3]]
       and sql("b", "SELECT count(*) FROM fed_log WHERE event='chain.gap'")[0][0] == 0)

    # --- idempotent, veraltet, Luecke (signiert von Hand mit dem Schluessel von A) ---------------------------------
    priva = fedsig.unb64u(py("a", "from saga import federation, fedsig\npriv, pub = federation.own_key()\nprint(json.dumps(fedsig.b64u(priv)))"))
    kid = T.I["a"]["url"]

    def post_ev(evs):
        s, h, d, _, _ = T.signed("b", priva, kid, "POST", "/fed/v2/events", body=json.dumps({"events": evs}).encode())
        return s, jd(d)
    ev = {"type": "test.noop", "id": "ab" * 16, "chain": "t3", "cseq": 1}
    r1, r2 = post_ev([ev]), post_ev([ev])
    ok(f"Ereignis einmal verarbeitet, Wiederholung -> dup ({r1[1]}, {r2[1]})", r1[1]["results"] == ["ignored"] and r2[1]["results"] == ["dup"])
    r3 = post_ev([dict(ev, id="cd" * 16, cseq=1)])
    ok("aeltere Nummer derselben Kette -> stale", r3[1]["results"] == ["stale"])
    post_ev([dict(ev, id="ef" * 16, cseq=5)])
    ok("Luecke in der Kette wird protokolliert", sql("b", "SELECT count(*) FROM fed_log WHERE event='chain.gap'")[0][0] == 1)
    ok("ungueltige Kennung -> ignored", post_ev([dict(ev, id="xyz", cseq=9)])[1]["results"] == ["ignored"])
    ok("unsigniert -> 401", hreq("b", "POST", "/fed/v2/events", body={"events": []})[0] == 401)

    # --- Rueckfall auf den Ausgangskorb (B kurz weg) ----------------------------------------------------------------
    s, d = api("a", "fa-anna", "POST", "/api/notes", {"title": "Offline-Seite", "body": T.blocks(T.P("y"))})
    nid2 = d["id"]
    api("a", "fa-anna", "PUT", f"/api/notes/{nid2}/shares", {"type": "remote", "target": f"@bert@{HB}", "role": "view"})
    wait(lambda: sql("b", "SELECT count(*) FROM fed_inbound WHERE note_id=?", (nid2,))[0][0] == 1, 10, 0.2)
    T.procs["b"].terminate(); T.procs["b"].wait(15)
    s, _ = api("a", "fa-anna", "PUT", f"/api/notes/{nid2}/shares", {"type": "remote", "target": f"@bert@{HB}", "role": "edit"})
    ok("B weg: Rolle trotzdem geaendert", s == 200)
    ok("B weg: Ereignis wartet im Ausgangskorb (Express fehlgeschlagen)", wait(lambda: sql("a", "SELECT count(*) FROM fed_outbox WHERE last_error IS NOT NULL")[0][0] >= 1, 15))
    t0 = time.time()
    r = py("a", f"from saga import federation as F\nprint(json.dumps(F.send_now({pa}, {{'type': 'test.noop'}})))")
    ok(f"send_now mit B weg: False nach Frist ({time.time() - t0:.1f} s)", r is False)
    start("b")
    ok("B wieder da: Ausgangskorb zugestellt", wait(lambda: sql("b", "SELECT role FROM fed_inbound WHERE note_id=?", (nid2,)) == [["edit"]], 60))
    ok("A: Ausgangskorb leer", wait(lambda: sql("a", "SELECT count(*) FROM fed_outbox")[0][0] == 0, 30))
    return nid


# =====================================================================================================
def part_directory(A, B, pa, pb):
    ok("A: Adressbuch von B da (Admin, Bert, Anna, Anna; ohne Olga/deaktiviert)", wait(lambda: dirn("a") == 4, 30))
    ok("B: Adressbuch von A da (Admin, Anna, Carl)", wait(lambda: dirn("b") == 3, 30))
    names_b = sorted(r[0] for r in sql("b", "SELECT name FROM fed_directory"))
    ok(f"B: Lisa (Anzeigename = Anmeldename) und Mia (Anzeigename = E-Mail) fehlen ({names_b})",
       names_b == ["Administrator", "Anna Berg", "Carl Ohne"] and "example.org" not in json.dumps(sql("b", "SELECT * FROM fed_directory")))
    rows = sql("a", "SELECT handle, name, pub FROM fed_directory ORDER BY name, pub")
    ok("Handles pseudonym (~ + 16 Zeichen), nie Anmeldename", all(re.fullmatch(r"~[a-z2-7]{16}", r[0]) for r in rows)
       and not any("fb-" in json.dumps(r) for r in rows))
    priva = fedsig.unb64u(py("a", "from saga import federation, fedsig\npriv, pub = federation.own_key()\nprint(json.dumps(fedsig.b64u(priv)))"))
    s, h, d, _, _ = T.signed("b", priva, T.I["a"]["url"], "GET", "/fed/v2/directory", "since=0&cfg=")
    raw = d.decode()
    ok("GET /fed/v2/directory signiert -> 200, voll", s == 200 and jd(d)["full"] is True and len(jd(d)["entries"]) == 4)
    ok("Abgleich ohne Anmeldenamen, E-Mail, Gruppen", "fb-" not in raw and "example.org" not in raw and "fbteam" not in raw and "groups" not in raw)
    nxt, cfg = jd(d)["next"], jd(d)["cfg"]
    s, h, d, _, _ = T.signed("b", priva, T.I["a"]["url"], "GET", "/fed/v2/directory", f"since={nxt}&cfg={cfg}")
    ok("inkrementell: seit Stand X nichts Neues", s == 200 and jd(d)["full"] is False and jd(d)["entries"] == [])
    ok("unsigniert -> 401", hreq("b", "GET", "/fed/v2/directory")[0] == 401)
    n = py("a", f"""
import time
from saga import federation as F, fed_dir as D
n = [0]
_cj = F.call_json
def cj(*a, **k):
    n[0] += 1; time.sleep(0.3); return _cj(*a, **k)
F.call_json = cj
for _ in range(30):
    D.pull_bg({pa}, True)
t0 = time.time()
while D._running and time.time() - t0 < 30:
    time.sleep(0.1)
print(json.dumps(n[0]))
""")
    ok(f"Flut 'directory.changed': je Instanz ein Abgleich + einer nachgeholt ({n} Abrufe)", isinstance(n, int) and 1 <= n <= 4)

    s, d = api("a", "fa-anna", "GET", "/api/chat/people?q=anna")
    rem = d.get("remote") or []
    ok(f"Chat-Suche: beide Anna Berg von B mit Herkunft ({[x['label'] for x in rem]})", len(rem) == 2
       and all(x["label"].startswith(f"Anna Berg · {HB}") for x in rem) and rem[0]["label"] != rem[1]["label"])
    ok("Chat-Suche: Treffer mit Handle ~x@host", all(re.fullmatch(r"~[a-z2-7]{16}@" + re.escape(HB), x["handle"]) for x in rem))
    s, d = api("a", "fa-anna", "GET", "/api/share/targets?q=anna")
    tg = [x["target"] for x in d.get("remote") or []]
    # 2.3 E2: Teilen geht auch an Personen ohne oeffentlichen Namen - Ziel ist dann der pseudonyme Handle (nie Anmeldename)
    ok(f"Teilen-Suche: beide, ohne oeffentlichen Namen ueber den Handle ({tg})", len(tg) == 2 and f"@annab@{HB}" in tg
       and any(re.fullmatch(r"@~[a-z2-7]{16}@" + re.escape(HB), x) for x in tg) and not any("fb-anna" in x for x in tg))
    s, d = api("a", "fa-anna", "GET", "/api/fed/people?q=olga")
    ok("Olga (Schalter aus) nicht auffindbar", s == 200 and d["people"] == [])
    s, d = api("a", "fa-anna", "GET", "/api/fed/people?q=dieter")
    ok("deaktiviertes Konto nicht auffindbar", d["people"] == [])
    s, d = api("a", "fa-anna", "GET", "/api/fed/people?q=gast")
    ok("Suche nach Anzeigename (Teil) findet Bert", [x["name"] for x in d["people"]] == ["Bert Gast"])
    s, d = api("a", "fa-anna", "POST", "/api/notes", {"title": "Fuer Anna B", "body": T.blocks(T.P("z"))})
    s, _ = api("a", "fa-anna", "PUT", f"/api/notes/{d['id']}/shares", {"type": "remote", "target": f"@annab@{HB}", "role": "view"})
    ok("Teilen mit Treffer aus dem Adressbuch", s == 200)

    # --- Schalter je Konto ----------------------------------------------------------------------------------------
    s, d = api("b", "fb-bert", "PUT", "/api/account/federation", {"directory": False})
    ok("B: Bert schaltet Adressbuch aus", s == 200 and d["directory"] is False)
    ok("A: Bert sofort weg (Ereignis)", wait(lambda: not api("a", "fa-anna", "GET", "/api/fed/people?q=bert")[1]["people"], 20))
    api("b", "fb-bert", "PUT", "/api/account/federation", {"directory": True})
    ok("A: Bert wieder da", wait(lambda: api("a", "fa-anna", "GET", "/api/fed/people?q=bert")[1]["people"], 20))
    s, d = api("b", "fb-bert", "GET", "/api/account/federation")
    ok("Konto-Stand: Schalter an, eigener Handle ~x@host", d["directory"] is True and re.fullmatch(r"~[a-z2-7]{16}@" + re.escape(HB), d["handle"] or ""))

    # --- Profilbild -----------------------------------------------------------------------------------------------
    s, _, _ = hreq("b", "PUT", "/api/account/avatar", raw=png(), hdr={"X-Saga": "1", "Content-Type": "image/png"}, cookie=T.ck("b", "fb-bert"))
    ok(f"B: Bert laedt Profilbild hoch ({s})", s == 200)
    py("b", "from saga import fed_dir\nprint(json.dumps(fed_dir.refresh_local(force=True)))")
    ok("A: Bild-Stand im Adressbuch", wait(lambda: sql("a", "SELECT av FROM fed_directory WHERE name='Bert Gast'")[0][0] != "", 20))
    d = api("a", "fa-anna", "GET", "/api/fed/people?q=bert")[1]
    url = d["people"][0].get("avatar") if d["people"] else None
    s, h, img = hreq("a", "GET", url, cookie=T.ck("a", "fa-anna")) if url else (0, {}, b"")
    ok(f"A: Profilbild von Bert ueber A ({s})", s == 200 and h.get("content-type", "").startswith("image/"))

    # --- deaktiviert -> Grabstein ------------------------------------------------------------------------------------
    sql("b", "UPDATE users SET disabled=1 WHERE username='fb-anna2'")
    py("b", "from saga import fed_dir\nprint(json.dumps(fed_dir.refresh_local(force=True)))")
    ok("A: deaktivierte Anna B verschwindet", wait(lambda: dirn("a") == 3, 20))
    sql("b", "UPDATE users SET disabled=0 WHERE username='fb-anna2'")
    py("b", "from saga import fed_dir\nprint(json.dumps(fed_dir.refresh_local(force=True)))")
    ok("A: wieder aktiv -> wieder da", wait(lambda: dirn("a") == 4, 20))

    # --- Admin-Schalter "Adressbuch" je Instanz -------------------------------------------------------------------------
    _, html = A.post({"action": "opts", "id": str(pa), "chat": "1", "calls": "1"})
    ok("A: Adressbuch mit B aus (Formular)", "Gespeichert" in T.flash(html))
    ok("A: fremdes Adressbuch von B sofort geloescht", dirn("a") == 0)
    ok("B: Adressbuch von A geloescht (403 beim Abgleich)", wait(lambda: dirn("b") == 0, 20))
    s, d = api("a", "fa-anna", "GET", "/api/fed/people?q=bert")
    ok("A: Suche ohne Treffer von B", d["people"] == [])
    A.post({"action": "opts", "id": str(pa), "chat": "1", "calls": "1", "directory": "1"})
    ok("A: wieder an -> beide Adressbuecher zurueck", wait(lambda: dirn("a") == 4 and dirn("b") == 3, 30))

    # --- Gruppen-Beschraenkung je Instanz ----------------------------------------------------------------------------
    admin_json(B, "PUT", f"/api/federation/peers/{pb}", {"directory_groups": ["fbteam"]})
    ok("B: nur Gruppe fbteam fuer A -> A sieht nur Bert", wait(lambda: [r[0] for r in sql("a", "SELECT name FROM fed_directory")] == ["Bert Gast"], 20))
    admin_json(B, "PUT", f"/api/federation/peers/{pb}", {"directory_groups": []})
    ok("B: Beschraenkung weg -> alle wieder", wait(lambda: dirn("a") == 4, 20))

    # --- "Teilen nur in gemeinsamen Gruppen" + Ausnahme je Instanz ---------------------------------------------------------
    setting("a", "share_group_scope", True)
    time.sleep(5.6)
    s, d = api("a", "fa-anna", "POST", "/api/notes", {"title": "Scope-Seite", "body": T.blocks(T.P("s"))})
    snid = d["id"]
    s, _ = api("a", "fa-anna", "PUT", f"/api/notes/{snid}/shares", {"type": "remote", "target": f"@bert@{HB}", "role": "view"})
    ok(f"nur gemeinsame Gruppen: Teilen mit B gesperrt ({s})", s == 403)
    ok("nur gemeinsame Gruppen: keine Treffer von B", not api("a", "fa-anna", "GET", "/api/fed/people?q=bert")[1]["people"]
       and not api("a", "fa-anna", "GET", "/api/share/targets?q=bert")[1].get("remote"))
    s, d = api("a", "fa-anna", "GET", f"/api/notes/{snid}/shares")
    ok("Teilen-Dialog: Foederation aus", d["federation"] is False)
    admin_json(A, "PUT", f"/api/federation/peers/{pa}", {"scope_exception": True})
    s, _ = api("a", "fa-anna", "PUT", f"/api/notes/{snid}/shares", {"type": "remote", "target": f"@bert@{HB}", "role": "view"})
    ok(f"Ausnahme fuer B: Teilen wieder erlaubt ({s})", s == 200)
    ok("Ausnahme fuer B: Bert auffindbar (Chat und Teilen)", api("a", "fa-anna", "GET", "/api/fed/people?q=bert")[1]["people"]
       and api("a", "fa-anna", "GET", "/api/share/targets?q=bert")[1].get("remote"))
    ok("Teilen-Dialog: Foederation an", api("a", "fa-anna", "GET", f"/api/notes/{snid}/shares")[1]["federation"] is True)
    admin_json(A, "PUT", f"/api/federation/peers/{pa}", {"scope_exception": False})
    setting("a", "share_group_scope", False)
    time.sleep(5.6)


# =====================================================================================================
def chat_user(k, uid):
    ident, dev = C.identity(), C.device()
    s, d = api(k, uid, "PUT", "/api/chat/identity", C.setup_body(uid, ident, dev, ops=1, mem=8 << 20))
    ok(f"{k.upper()}: {uid} richtet Chats ein ({s})", s == 200)
    return ident, dev


def part_keys(A, B, pa, pb):
    ia, da = chat_user("a", "fa-anna")
    ib, db = chat_user("b", "fb-bert")
    s, me = api("b", "fb-bert", "GET", "/api/chat/v2/me")
    hb = me["handle"]
    ok("v2/me: Handle ~x@host, Zertifikat fehlt", s == 200 and SV.HANDLE_RE.fullmatch(hb) and hb.endswith("@" + HB) and me["need"] and not me["cert_ok"])
    s, _ = api("b", "fb-bert", "PUT", "/api/chat/v2/cert", {"handle_sig": C.b64(C.handle_sig(hb.replace("~", "~a", 1)[:-1], ib))})
    ok("falsches Handle-Zertifikat -> 403", s == 403)
    s, _ = api("b", "fb-bert", "PUT", "/api/chat/v2/cert", {"devices": {db["id"]: C.b64(C.sign(C.device_msg("fb-bert", db["id"], db["enc_pk"], db["sign_pk"]), ib["ident_sk"]))}})
    ok("Geraete-Zertifikat v1 statt v2 -> 403", s == 403)
    s, me = api("b", "fb-bert", "PUT", "/api/chat/v2/cert", {"handle_sig": C.b64(C.handle_sig(hb, ib)), "devices": {db["id"]: C.b64(C.device_sig_v2(hb, ib, db))}})
    ok("Zertifikate v2 hochgeladen (Geraete-Liste fehlt noch)", s == 200 and me["cert_ok"] and not me["missing"] and not me["device_set_ok"])
    # 2.3 E2 (Review): aktive Geraete nur ueber die signierte Geraete-Liste - erst danach ist nichts mehr offen
    s, me = api("b", "fb-bert", "PUT", "/api/chat/v2/cert", {"device_set": C.devices_sig_v2(hb, ib, 1, me["device_set_want"])})
    ok("Geraete-Liste signiert -> nichts mehr offen", s == 200 and me["device_set_ok"] and not me["need"])
    s, mea = api("a", "fa-anna", "GET", "/api/chat/v2/me")
    ha = mea["handle"]
    api("a", "fa-anna", "PUT", "/api/chat/v2/cert", {"handle_sig": C.b64(C.handle_sig(ha, ia)), "devices": {da["id"]: C.b64(C.device_sig_v2(ha, ia, da))}})

    s, d = api("a", "fa-anna", "GET", f"/api/chat/v2/keys?handles={hb}")
    e = d["keys"].get(hb) if s == 200 else None
    ok(f"A holt Schluessel von Bert ueber den eigenen Server ({(e or {}).get('source')})", e and e["source"] == "remote" and len(e["devices"]) == 1)
    ok("Zertifikate pruefbar (Handle + Geraet)", e and SV.verify_handle(hb, e["ident_pub"], e["backup_pub"], e["sig"])
       and SV.verify_device(hb, e["ident_pub"], e["devices"][0]["id"], e["devices"][0]["enc_pub"], e["devices"][0]["sign_pub"], e["devices"][0]["fed_sig"]))
    ok("zweiter Abruf aus dem Zwischenspeicher", api("a", "fa-anna", "GET", f"/api/chat/v2/keys?handles={hb}")[1]["keys"][hb]["source"] == "cache")
    s, d = api("b", "fb-bert", "GET", f"/api/chat/v2/keys?handles={hb}")
    ok("eigene Instanz: source local", d["keys"][hb]["source"] == "local")
    unknown = "~aaaaaaaaaaaaaaaa@" + HB
    ok("unbekannter Handle -> null", api("a", "fa-anna", "GET", f"/api/chat/v2/keys?handles={unknown}")[1]["keys"][unknown] is None)
    # Ende-zu-Ende mit den geholten Schluesseln
    ref = "0123456789abcdef0123456789abcdef@" + HA
    m = C.encrypt_v2(ref, ha, da, C.recipients_v2([e]), json.dumps({"v": 1, "type": "text", "text": "Hallo Bert"}))
    ok("v2: Empfaengerkennungen dev:<id>@host + backup:<handle>", sorted(m["keys"]) == sorted([f"dev:{db['id']}@{HB}", "backup:" + hb]))
    ok("v2: Bert oeffnet mit Geraet und Backup", json.loads(C.decrypt_v2(ref, m, m["keys"][f"dev:{db['id']}@{HB}"], db["enc_pk"], db["enc_sk"]))["text"] == "Hallo Bert"
       and json.loads(C.decrypt_v2(ref, m, m["keys"]["backup:" + hb], ib["backup_pk"], ib["backup_sk"]))["text"] == "Hallo Bert")
    ok("v2: Signatur mit Geraeteschluessel von A pruefbar (Server-Referenz)", SV.verify_message(C.b64(da["sign_pk"]), ref, m))
    ok("Sicherheitsnummer v2 auf beiden Seiten gleich", C.safety_number_v2(ha, ia["ident_pk"], hb, ib["ident_pk"]) == SV.safety_number(hb, ib["ident_pk"], ha, ia["ident_pk"]))
    # Schluesselwechsel: neues Geraet bei B -> chat.keys.changed -> A holt neu
    d2 = C.device()
    s, _ = api("b", "fb-bert", "POST", "/api/chat/devices", C.device_api("fb-bert", ib, d2, "Zweitgeraet"))
    ok("B: zweites Geraet", s == 200)
    ok("A: Zwischenspeicher veraltet (chat.keys.changed)", wait(lambda: sql("a", "SELECT stale FROM fed_chat_keys WHERE handle=?", (hb,)) == [[1]], 20))
    s, me = api("b", "fb-bert", "GET", "/api/chat/v2/me")
    ok("v2/me: zweites Geraet ohne Zertifikat v2", me["need"] and me["missing"] == [d2["id"]])
    api("b", "fb-bert", "PUT", "/api/chat/v2/cert", {"devices": {d2["id"]: C.b64(C.device_sig_v2(hb, ib, d2))}})
    ok("A: neu geholt mit zwei Geraeten", wait(lambda: len((api("a", "fa-anna", "GET", f"/api/chat/v2/keys?handles={hb}")[1]["keys"].get(hb) or {}).get("devices", [])) == 2, 20))
    # Chat-Schalter je Instanz
    admin_json(B, "PUT", f"/api/federation/peers/{pb}", {"chat": False})
    ok("B: Chat fuer A aus -> A bekommt keine Schluessel (Zwischenspeicher weg)",
       api("a", "fa-anna", "GET", f"/api/chat/v2/keys?handles={hb}&fresh=1")[1]["keys"][hb] is None and not sql("a", "SELECT 1 FROM fed_chat_keys"))
    admin_json(B, "PUT", f"/api/federation/peers/{pb}", {"chat": True})
    ok("B: Chat wieder an -> Schluessel wieder da", api("a", "fa-anna", "GET", f"/api/chat/v2/keys?handles={hb}&fresh=1")[1]["keys"][hb] is not None)
    # lokale v1-Chats unveraendert
    s, d = api("a", "fa-anna", "GET", "/api/chat/me")
    ok("v1: Geraet weiter mit v1-Zertifikat", s == 200 and d["devices"][0]["sig"] and C.verify(C.unb64(d["devices"][0]["sig"]),
                                                                                              C.device_msg("fa-anna", da["id"], da["enc_pk"], da["sign_pk"]), ia["ident_pk"]))
    return hb


def part_disconnect(A, B, pa, pb):
    _, html = A.post({"action": "disconnect", "id": str(pa)})
    ok("A trennt", "getrennt" in T.flash(html))
    left = (dirn("a"), sql("a", "SELECT handle, peer_id FROM fed_chat_keys"), sql("a", "SELECT id, status FROM fed_peer"))
    ok(f"A: fremdes Adressbuch sofort weg, Schluessel-Zwischenspeicher weg {left}", left[0] == 0 and not left[1])
    ok("B: Adressbuch von A sofort weg", wait(lambda: dirn("b") == 0, 10))
    ok("A: Suche ohne Treffer", not api("a", "fa-anna", "GET", "/api/fed/people?q=bert")[1]["people"])


def run():
    try:
        A, B, pa, pb = main()
        if COMPAT:
            part_compat(A, B, pa, pb)
        else:
            part_protocol(A, B, pa, pb)
            part_directory(A, B, pa, pb)
            part_keys(A, B, pa, pb)
            part_disconnect(A, B, pa, pb)
    except Exception:
        import traceback
        traceback.print_exc()
        T.res.append(False)
    finally:
        T.stop_all()
        for k in T.I:
            n, txt = T.server_errors(k)
            ok(f"{k.upper()}: Serverfehler {n}", n == 0)
            if n:
                print(txt, flush=True)
        for k in T.I:
            try:
                T.pg_admin(f"DROP DATABASE IF EXISTS {T.I[k]['db']} WITH (FORCE)")
            except Exception as ex:  # noqa: BLE001
                print("aufraeumen:", ex)
    res = T.res
    print(f"\n{sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    run()
