"""Live: eingebettete Datenbank aendert die umgebende Seite nicht (2.5, Fund "eigene Aenderung als fremd gemeldet").

Seite mit eingebettetem Datenbank-Block (views: null, Datenbank als Unterseite). Zeile anlegen, Titel/Eigenschaft
aendern, Zeile loeschen, Schema der Datenbank aendern -> Revision UND updated der Seite bleiben, an einen zweiten Tab
desselben Kontos geht kein note.changed fuer die Seite (db.rows dagegen ja). Nur Symbol geaendert -> note.changed mit
meta: true und gleicher rev; Inhalt geaendert -> ohne meta, rev + 1; der ausloesende Tab bekommt beides nicht zurueck.
Wegwerf-Konto le-a; raeumt auf. Umgebung wie tests/live.py (HK_TEST_BASE, BASE_URL, SESSION_SECRET)."""
import json, secrets, sys, time
import auth
from db import connect
from live import Stream, STREAMS, ck, req

res = []


def ok(n, c):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, flush=True)


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'le-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        for t in ("note", "note_version", "user_kv", "notification"):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE 'le-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'le-%'")


def main():
    cleanup()
    auth.create_user("le-a", "Live-Pass-1234", "Lena Eingebettet")
    A = ck("le-a")
    T1, T2 = "letab" + secrets.token_hex(6), "letab" + secrets.token_hex(6)
    H1 = {"X-Saga-Tab": T1}
    pg = req("POST", "/api/notes", A, {"title": "Seite mit Tabelle"}, hdr=H1)[1]["id"]
    db = req("POST", "/api/notes", A, {"title": "Aufgaben", "kind": "database", "parent_id": pg}, hdr=H1)[1]["id"]
    s, _ = req("PATCH", f"/api/notes/{db}", A, {"schema": {"props": [{"id": "st", "name": "Status", "type": "select",
                                                                       "options": [{"id": "of", "name": "Offen", "color": "gray"},
                                                                                   {"id": "oa", "name": "In Arbeit", "color": "blue"}]}]}}, hdr=H1)
    body = json.dumps({"blocks": [{"id": "e0", "type": "paragraph", "data": {"text": "Oben"}},
                                  {"id": "e1", "type": "database", "data": {"db": db, "view": None, "views": None}}]})
    s, d = req("PATCH", f"/api/notes/{pg}", A, {"body": body, "format": "blocks"}, hdr=H1)
    ok(f"Seite mit Datenbank-Block angelegt ({s})", s == 200)
    row = req("POST", "/api/notes", A, {"title": "Zeile 1", "parent_id": db}, hdr=H1)[1]["id"]
    time.sleep(2.3)                                   # gesammelte notes.tree/db.rows vom Anlegen abwarten
    st1, st2 = Stream(A, tab=T1), Stream(A, tab=T2)
    ok("Zwei Tabs verbunden", st1.wait("hello") is not None and st2.wait("hello") is not None)
    s, _ = req("POST", "/api/presence", A, {"note_id": pg, "editing": True}, hdr=H1)
    ok(f"Anwesenheit auf der Seite ({s})", s == 200)
    time.sleep(0.5)
    g0 = req("GET", f"/api/notes/{pg}", A)[1]
    m1, m2 = st1.mark(), st2.mark()

    # --- Zellen/Zeilen/Schema der eingebetteten Datenbank -----------------------------------------------------------
    s1 = req("PATCH", f"/api/notes/{row}", A, {"title": "Zeile 1 neu"}, hdr=H1)[0]
    s2 = req("PATCH", f"/api/notes/{row}", A, {"props": {"st": "oa"}}, hdr=H1)[0]
    s3, r2 = req("POST", "/api/notes", A, {"title": "Zeile 2", "parent_id": db}, hdr=H1)
    s4 = req("DELETE", f"/api/notes/{r2['id']}", A, hdr=H1)[0]
    sch = req("GET", f"/api/notes/{db}", A)[1]["schema"]            # Schema: Spalte dazu
    sch["props"].append({"id": "nz", "name": "Notiz", "type": "text"})
    s5, e5 = req("PATCH", f"/api/notes/{db}", A, {"schema": sch}, hdr=H1)
    ok(f"Aenderungen an der Datenbank angenommen ({s1}, {s2}, {s3}, {s4}, {s5} {e5 if s5 != 200 else ''})", (s1, s2, s3, s4, s5) == (200, 200, 200, 200, 200))
    rows = st2.wait("db.rows", lambda x: x.get("db") == db, since=m2, timeout=5)
    ok(f"Zweiter Tab: db.rows fuer die Datenbank ({rows})", rows is not None)
    time.sleep(2.5)
    g1 = req("GET", f"/api/notes/{pg}", A)[1]
    ok(f"Seite: Revision bleibt ({g0['rev']} -> {g1['rev']})", g1["rev"] == g0["rev"])
    ok(f"Seite: updated bleibt ({g0['updated']} -> {g1['updated']})", g1["updated"] == g0["updated"])
    ok("Seite: Inhalt unveraendert (Block mit views null)", g1["body"] == g0["body"])
    pe = [e[1] for e in st1.ev[m1:] + st2.ev[m2:] if e[0] == "note.changed" and e[1].get("id") == pg]
    ok(f"Kein note.changed fuer die Seite an irgendeinen Tab ({pe})", pe == [])

    # --- meta: nur Symbol geaendert ----------------------------------------------------------------------------------
    m1, m2 = st1.mark(), st2.mark()
    s = req("PATCH", f"/api/notes/{pg}", A, {"icon": "🧪"}, hdr=H1)[0]
    ev = st2.wait("note.changed", lambda x: x.get("id") == pg, since=m2)
    ok(f"Symbol: zweiter Tab bekommt note.changed mit meta und gleicher rev ({s}, {ev})",
       s == 200 and ev is not None and ev.get("meta") is True and ev.get("rev") == g0["rev"])
    time.sleep(1.0)
    ok("Symbol: ausloesender Tab bekommt es nicht zurueck", st1.find("note.changed", lambda x: x.get("id") == pg, since=m1) is None)

    # --- Inhalt geaendert: ohne meta, rev + 1 ------------------------------------------------------------------------
    m1, m2 = st1.mark(), st2.mark()
    body2 = json.dumps({"blocks": [{"id": "e0", "type": "paragraph", "data": {"text": "Oben neu"}},
                                   {"id": "e1", "type": "database", "data": {"db": db, "view": None, "views": None}}]})
    s, d = req("PATCH", f"/api/notes/{pg}", A, {"body": body2, "format": "blocks", "rev": g0["rev"]}, hdr=H1)
    ev = st2.wait("note.changed", lambda x: x.get("id") == pg, since=m2)
    ok(f"Inhalt: note.changed ohne meta, rev + 1 ({s}, {ev})",
       s == 200 and ev is not None and not ev.get("meta") and ev.get("rev") == g0["rev"] + 1)
    time.sleep(1.0)
    ok("Inhalt: ausloesender Tab bekommt es nicht zurueck", st1.find("note.changed", lambda x: x.get("id") == pg, since=m1) is None)

    for x in list(STREAMS):
        x.close()
    cleanup()
    print(f"\nLIVE-EINGEBETTET {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        for x in list(STREAMS):
            x.close()
        cleanup()
        raise
