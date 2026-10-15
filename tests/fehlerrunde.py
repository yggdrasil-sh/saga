"""Regressionstests der Fehlerrunde nach dem Bughunt 2.0 (BUG-xxx aus der Triage).

Wegwerf-Konten fr-*, raeumt auf. Umgebung wie die anderen Tests (HK_TEST_BASE, BASE_URL, SESSION_SECRET)."""
import base64, http.client, json, os, sys, time, urllib.parse
from itsdangerous import TimestampSigner
import auth
from db import connect

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
PW = "Fehler-Pass-1234"
WAIT = 5.6                       # Einstellungen sind im Server 5 s zwischengespeichert
res = []


def ok(n, c):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, flush=True)


def ck(u, **extra):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5, **extra}).encode())).decode()


def req(m, p, cookie=None, body=None, hdr=None, raw_body=None):
    """-> (status, daten: JSON, Text oder Bytes)"""
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=30)
    h = {"Host": HOST, "Accept-Language": "de"}
    if cookie:
        h["Cookie"] = "saga=" + cookie; h["X-Saga"] = "1"
    if body is not None:
        h["Content-Type"] = "application/json"; raw_body = json.dumps(body)
    h.update(hdr or {})
    c.request(m, p, body=raw_body, headers=h)
    r = c.getresponse(); data = r.read()
    ct = r.getheader("content-type") or ""
    if "json" in ct:
        try:
            data = json.loads(data)
        except ValueError:
            pass
    elif ct.startswith("text/"):
        data = data.decode("utf-8", "replace")
    return r.status, data


def raw(m, p, cookie, text):
    """Roher JSON-Text (z. B. mit \\u0000 oder Infinity, das json.dumps so nicht erzeugt)."""
    return req(m, p, cookie, hdr={"Content-Type": "application/json"}, raw_body=text.encode())


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'fr-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        for t in ("task", "note", "note_version", "user_kv", "device_login", "app_token"):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE 'fr-%'")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'fr-%' OR target LIKE 'fr-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'fr-%'")


def schema_of(cookie, nid):
    return req("GET", f"/api/notes/{nid}", cookie)[1]["schema"]


def t_bug001(A, Bk):
    """Veraltetes Schema loescht keine Spalten/Optionen, die inzwischen jemand anderes angelegt hat."""
    db = req("POST", "/api/notes", A, {"title": "FR Datenbank", "kind": "database"})[1]["id"]
    s, _ = req("PUT", f"/api/notes/{db}/shares", A, {"type": "user", "target": "fr-b", "role": "edit"})
    ok(f"BUG-001: Datenbank mit B geteilt ({s})", s == 200)
    s, d = req("PATCH", f"/api/notes/{db}", A, {"schema": {"props": [
        {"id": "pa", "name": "Alt", "type": "text"},
        {"id": "ps", "name": "Status", "type": "select", "options": [{"id": "o1", "name": "Offen", "color": "gray"}]}]}})
    ok(f"BUG-001: Schema-PATCH liefert srev ({s}, {d})", s == 200 and isinstance(d.get("srev"), int))
    stale = schema_of(A, db)                                  # Stand von A (Tab offen)
    row = req("POST", "/api/notes", A, {"title": "Zeile 1", "parent_id": db})[1]["id"]
    # B legt Spalte und Option an und fuellt beide
    sb = schema_of(Bk, db)
    sb["props"].append({"id": "pb", "name": "Von B", "type": "text"})
    next(p for p in sb["props"] if p["id"] == "ps")["options"].append({"id": "o2", "name": "Fertig", "color": "green"})
    s, _ = req("PATCH", f"/api/notes/{db}", Bk, {"schema": sb})
    s2, _ = req("PATCH", f"/api/notes/{row}", Bk, {"props": {"pb": "Wert von B", "ps": "o2"}})
    ok(f"BUG-001: B legt Spalte/Option an und fuellt sie ({s}, {s2})", s == 200 and s2 == 200)
    # A benennt mit veraltetem Schema eine Spalte um
    stale["props"][0]["name"] = "Alt umbenannt"
    s, _ = req("PATCH", f"/api/notes/{db}", A, {"schema": stale})
    now = schema_of(A, db)
    ids = [p["id"] for p in now["props"]]
    opts = [o["id"] for o in next(p for p in now["props"] if p["id"] == "ps")["options"]]
    ok(f"BUG-001: Umbenennen mit altem Stand ({s}) behaelt Spalte/Option von B: {ids} {opts}",
       s == 200 and ids == ["pa", "ps", "pb"] and opts == ["o1", "o2"] and now["props"][0]["name"] == "Alt umbenannt")
    props = req("GET", f"/api/notes/{row}", A)[1]["props"]
    ok(f"BUG-001: Werte von B bleiben erhalten: {props}", props.get("pb") == "Wert von B" and props.get("ps") == "o2")
    # Mit aktuellem Stand loescht A die Spalte von B ausdruecklich -> weg
    cur = schema_of(A, db)
    cur["props"] = [p for p in cur["props"] if p["id"] != "pb"]
    s, _ = req("PATCH", f"/api/notes/{db}", A, {"schema": cur})
    ok(f"BUG-001: mit aktuellem Stand ausdruecklich geloescht ({s})",
       s == 200 and [p["id"] for p in schema_of(A, db)["props"]] == ["pa", "ps"] and "pb" not in req("GET", f"/api/notes/{row}", A)[1]["props"])
    # Eigene neue Spalte im selben (nicht neu geladenen) Stand wieder loeschen: klappt (Web/Linux laden nicht nach)
    mine = schema_of(A, db)
    mine["props"].append({"id": "pm", "name": "Meine", "type": "text"})
    req("PATCH", f"/api/notes/{db}", A, {"schema": mine})
    mine["props"] = [p for p in mine["props"] if p["id"] != "pm"]
    s, _ = req("PATCH", f"/api/notes/{db}", A, {"schema": mine})
    ok(f"BUG-001: eigene Spalte ohne Neuladen wieder geloescht ({s})", s == 200 and "pm" not in [p["id"] for p in schema_of(A, db)["props"]])
    # Aeltere Clients ohne srev: Schema ohne srev/_since -> eigene Spalten weiter loeschbar
    plain = {"props": [p for p in schema_of(A, db)["props"] if p["id"] != "pa"]}
    s, _ = req("PATCH", f"/api/notes/{db}", A, {"schema": plain})
    ok(f"BUG-001: ohne srev (aeltere App) eigene Spalte geloescht ({s})", s == 200 and "pa" not in [p["id"] for p in schema_of(A, db)["props"]])
    ok("BUG-001: kein Anmeldename im Schema", "fr-a" not in json.dumps(schema_of(A, db)) and "fr-b" not in json.dumps(schema_of(A, db)))


def t_bug002(A, Bk):
    """Geraet abmelden beendet dessen Sitzung sofort."""
    from saga.app_login import _devlogin_db
    with connect() as c, c:
        _devlogin_db(c)
        did = c.execute("INSERT INTO device_login (uid, uname, name, hash, prefix, created, last_used) VALUES (?,?,?,?,?,?,?)",
                        ("fr-a", "fr-a", "FR Testgeraet", "fr-hash-" + str(time.time()), "hkd_frtest", time.time(), time.time())).lastrowid
    D = ck("fr-a", did=did)
    ok("BUG-002: Geraete-Sitzung gilt", req("GET", "/api/notes", D)[0] == 200)
    s, _ = req("DELETE", f"/api/settings/devices/{did}", A)
    ok(f"BUG-002: Geraet abgemeldet ({s})", s == 200)
    s, _ = req("GET", "/api/notes", D)
    ok(f"BUG-002: Sitzung des abgemeldeten Geraets -> {s}", s == 401)
    ok("BUG-002: Browser-Sitzung (ohne Geraet) bleibt", req("GET", "/api/notes", A)[0] == 200)
    ok("BUG-002: unbekanntes Geraet in der Sitzung -> 401", req("GET", "/api/notes", ck("fr-a", did=987654321))[0] == 401)


# --- weitere Tests oberhalb einfuegen ----------------------------------------------------------------------


def main():
    cleanup()
    prev = auth.settings(fresh=True)
    prev_en, prev_scope = prev.get("share_enabled", True), prev.get("share_group_scope", False)
    auth.save_setting("share_enabled", True); auth.save_setting("share_group_scope", False)
    auth.create_user("fr-a", PW, "Fiona Fehler")
    auth.create_user("fr-b", PW, "Bruno Fehler")
    A, Bk = ck("fr-a"), ck("fr-b")
    time.sleep(WAIT)
    try:
        for name, fn in [(n, f) for n, f in globals().items() if n.startswith("t_bug") and callable(f)]:
            try:
                fn(A, Bk)
            except Exception as ex:  # noqa: BLE001 - ein Test bricht nicht alle ab
                ok(f"{name}: Ausnahme {type(ex).__name__}: {ex}", False)
    finally:
        auth.save_setting("share_enabled", prev_en); auth.save_setting("share_group_scope", prev_scope)
        cleanup()
    print(f"\nFEHLERRUNDE {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
    sys.exit(0 if res and all(res) else 1)


if __name__ == "__main__":
    main()
