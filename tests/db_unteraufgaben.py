"""Datenbanken 2.5, Paket 5: Unteraufgaben und Abhaengigkeiten (zweiseitige Selbst-Relation mit Rollen).

Prueft: Schema mit two_way + role "parent" (bzw. "blocked_by") auf die EIGENE Datenbank legt die Gegenspalte im selben
Schema an (role children/blocking, pair gekoppelt), Rollen nur bei Selbst-Relationen; Werte spiegeln in beide
Richtungen (Kind setzt Elternteil -> Eltern bekommen das Kind, und umgekehrt); Elternteil hoechstens einer (neuer
Elterneintrag loest den alten); keine Selbstreferenz, kein Kreis (auch ueber "Unteraufgaben" gesetzt), Tiefe <= 10;
bestehende Werte werden beim Koppeln nachgetragen; Entkoppeln (two_way false) entfernt die Gegenspalte samt Werten;
Gegenspalte loeschen entkoppelt; view.sub (nested/parents_only) in /rows: Kinder hinter Eltern, depth/sub_of, Eltern
eines Treffers bleiben sichtbar, Blaettern stabil; "blocked": wahr, solange ein blocked_by-Eintrag nicht in Status-
Gruppe done ist (sonst Haekchen), auch in GET /api/notes/{zeile}; Rechte: geteilte Bearbeiter koennen Unteraufgaben
setzen, Leser nicht; Papierkorb (geloeschtes Kind nicht mehr in nested).
Wegwerf-Konten p5s-*, raeumt auf."""
import base64
import http.client
import json
import os
import sys
import time
import urllib.parse

from itsdangerous import TimestampSigner

import auth
from db import connect

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
LOG = os.path.join(os.environ.get("STATE_DIR", "/tmp"), "server.log")
A, BU = "p5s-a", "p5s-b"
res = []


def ok(n, c, detail=None):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, flush=True)
    if not c and detail is not None:
        print("     ", str(detail)[:1500], flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, u=A, body=None, raw=False):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=60)
    h = {"Host": HOST, "Accept-Language": "de", "X-Saga": "1"}
    if u:
        h["Cookie"] = "saga=" + ck(u)
    data = None
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    c.request(m, p, body=data, headers=h)
    r = c.getresponse()
    b = r.read()
    if raw:
        return r.status, b
    try:
        return r.status, json.loads(b) if b[:1] in (b"[", b"{") else b
    except ValueError:
        return r.status, b


def qs(**kw):
    return "?" + urllib.parse.urlencode({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v) for k, v in kw.items()})


def errs():
    try:
        with open(LOG, encoding="utf-8", errors="replace") as f:
            return f.read().count("Traceback")
    except OSError:
        return 0


def new_db(title, props, views=None):
    s, d = req("POST", "/api/notes", body={"kind": "database", "title": title})
    db = d["id"]
    s, d = req("PATCH", f"/api/notes/{db}", body={"schema": {"props": props, "views": views or [{"id": "t", "name": "T", "type": "table"}]}})
    assert s == 200, d
    return db


def row(db, title, props=None):
    s, d = req("POST", "/api/notes", body={"parent_id": db, "title": title, "props": props or {}})
    assert s == 200, (s, d)
    return d["id"]


def titles(db, **kw):
    s, d = req("GET", f"/api/notes/{db}/rows" + qs(**kw))
    return (sorted(r["title"] for r in d["rows"]) if s == 200 else (s, d)), d


def rows(db, **kw):
    s, d = req("GET", f"/api/notes/{db}/rows" + (qs(**kw) if kw else ""))
    assert s == 200, (s, d)
    return d


def props(i):
    return req("GET", f"/api/notes/{i}")[1].get("props") or {}


def run():
    st = {"id": "st", "name": "Status", "type": "select", "status": True, "options": [
        {"id": "o", "name": "Offen", "group": "todo"}, {"id": "l", "name": "Läuft", "group": "doing"}, {"id": "f", "name": "Fertig", "group": "done"}]}
    db = new_db("Aufgaben", [st])
    s, g = req("GET", f"/api/notes/{db}")
    sch = g["schema"]
    a, b, c_, d_, e = (row(db, x) for x in ("A", "B", "C", "D", "E"))
    # vorher schon einseitige Selbst-Relation mit Werten -> beim Koppeln nachtragen
    sch["props"].append({"id": "par", "name": "Übergeordnet", "type": "relation", "target": db})
    s, d = req("PATCH", f"/api/notes/{db}", body={"schema": sch})
    ok("einseitige Selbst-Relation geht wie bisher", s == 200, d)
    req("PATCH", f"/api/notes/{b}", body={"props": {"par": [a]}})
    s, g = req("GET", f"/api/notes/{db}")
    sch = g["schema"]
    p = next(x for x in sch["props"] if x["id"] == "par")
    p["two_way"], p["role"] = True, "parent"
    s, d = req("PATCH", f"/api/notes/{db}", body={"schema": sch})
    s, g = req("GET", f"/api/notes/{db}")
    sch = g["schema"]
    par = next(x for x in sch["props"] if x["id"] == "par")
    kids = next((x for x in sch["props"] if x.get("pair") == "par"), None)
    ok("Unteraufgaben aktivieren: Gegenspalte im selben Schema, Rollen parent/children, gekoppelt",
       s == 200 and kids and par.get("role") == "parent" and kids.get("role") == "children" and par.get("pair") == kids["id"]
       and kids["target"] == db, sch["props"])
    ok("bestehender Wert nachgetragen: A hat Kind B", props(a).get(kids["id"]) == [b], props(a))
    # Kind setzen ueber Eltern
    s, d = req("PATCH", f"/api/notes/{a}", body={"props": {kids["id"]: [b, c_]}})
    ok("Eltern setzt Kinder -> Kinder bekommen Elternteil", props(c_).get("par") == [a], props(c_))
    # Elternteil hoechstens einer: C bekommt D als neuen Elternteil -> A verliert C
    s, d = req("PATCH", f"/api/notes/{c_}", body={"props": {"par": [d_, a]}})
    ok("Elternteil hoechstens einer (erster gewinnt)", props(c_).get("par") == [d_], props(c_))
    ok("neuer Elternteil: D hat C, A verliert C", props(d_).get(kids["id"]) == [c_] and props(a).get(kids["id"]) == [b], (props(d_), props(a)))
    s, d = req("PATCH", f"/api/notes/{b}", body={"props": {kids["id"]: [e]}})
    s, d = req("PATCH", f"/api/notes/{a}", body={"props": {kids["id"]: [b, c_]}})
    ok("ueber Kinder-Spalte umhaengen: C wechselt von D zu A", props(c_).get("par") == [a] and not props(d_).get(kids["id"]), (props(c_), props(d_)))
    # Kreise
    s, d = req("PATCH", f"/api/notes/{a}", body={"props": {"par": [a]}})
    ok("Selbstbezug -> 400", s == 400, (s, d))
    s, d = req("PATCH", f"/api/notes/{a}", body={"props": {"par": [e]}})          # A -> B -> E, E als Elternteil von A = Kreis
    ok("Kreis ueber Eltern -> 400", s == 400, (s, d))
    s, d = req("PATCH", f"/api/notes/{e}", body={"props": {kids["id"]: [a]}})     # E bekommt A als Kind = Kreis
    ok("Kreis ueber Unteraufgaben -> 400", s == 400, (s, d))
    ok("nach Kreis-Versuch unveraendert", props(a).get("par") is None and props(e).get("par") == [b], (props(a), props(e)))
    chain = [row(db, f"K{i}") for i in range(11)]
    for i in range(1, 10):
        s, _ = req("PATCH", f"/api/notes/{chain[i]}", body={"props": {"par": [chain[i - 1]]}})
    s, d = req("PATCH", f"/api/notes/{chain[10]}", body={"props": {"par": [chain[9]]}})
    ok("Tiefe > 10 -> 400", s == 400, (s, d))
    s, d = req("POST", "/api/notes", body={"parent_id": db, "title": "Neu", "props": {"par": [a]}})
    ok("neue Zeile mit Elternteil -> Eltern bekommt sie", s == 200 and d["id"] in props(a).get(kids["id"], []), props(a))
    neu = d["id"]

    # Ansicht verschachtelt
    sch = req("GET", f"/api/notes/{db}")[1]["schema"]
    sch["views"] = [{"id": "n", "name": "Baum", "type": "table", "sub": {"parent": "par", "mode": "nested"}},
                    {"id": "o", "name": "Oben", "type": "list", "sub": {"parent": "par", "mode": "parents_only"}},
                    {"id": "x", "name": "Falsch", "type": "table", "sub": {"parent": kids["id"], "mode": "nested"}}]
    req("PATCH", f"/api/notes/{db}", body={"schema": sch})
    sch = req("GET", f"/api/notes/{db}")[1]["schema"]
    ok("view.sub nur mit Rolle parent", "sub" in sch["views"][0] and "sub" not in sch["views"][2], sch["views"])
    d = rows(db, view="n")
    seq = [(r["title"], r.get("depth"), r.get("sub_of")) for r in d["rows"]]
    names = [x[0] for x in seq]
    ok("nested: Kinder direkt hinter den Eltern, depth/sub_of",
       names[:5] == ["A", "B", "E", "C", "Neu"] and seq[0][1:] == (0, None) and seq[1][1:] == (1, a) and seq[2][1:] == (2, b), seq[:6])
    ok("nested: K0..K9 als Kette (Tiefe 9)", [x[1] for x in seq if x[0].startswith("K")][:10] == list(range(10)), seq)
    d2 = rows(db, view="n", filters=[{"pid": "title", "op": "is", "value": "E"}])
    ok("Filter trifft Kind: Eltern-Kette bleibt sichtbar", [r["title"] for r in d2["rows"]] == ["A", "B", "E"], [r["title"] for r in d2["rows"]])
    parts = []
    for off in range(0, 40, 4):
        parts += [r["title"] for r in rows(db, view="n", limit=4, offset=off)["rows"]]
    ok("Blaettern lueckenlos in nested", parts == names, (parts, names))
    d = rows(db, view="o")
    ok("parents_only: nur oberste Ebene", sorted(r["title"] for r in d["rows"]) == ["A", "D", "K0", "K10"], [r["title"] for r in d["rows"]])
    d = rows(db)
    ok("ohne view: keine depth-Felder (Antwort wie bisher)", all("depth" not in r for r in d["rows"]), d["rows"][:2])
    req("DELETE", f"/api/notes/{neu}")
    d = rows(db, view="n")
    ok("Papierkorb: geloeschtes Kind nicht mehr im Baum", "Neu" not in [r["title"] for r in d["rows"]])

    # Abhaengigkeiten
    sch = req("GET", f"/api/notes/{db}")[1]["schema"]
    sch["props"].append({"id": "blk", "name": "Blockiert durch", "type": "relation", "target": db, "two_way": True, "role": "blocked_by"})
    req("PATCH", f"/api/notes/{db}", body={"schema": sch})
    sch = req("GET", f"/api/notes/{db}")[1]["schema"]
    bing = next((x for x in sch["props"] if x.get("pair") == "blk"), {})
    ok("Abhaengigkeiten aktivieren: blocking-Gegenspalte", bing.get("role") == "blocking", sch["props"])
    req("PATCH", f"/api/notes/{c_}", body={"props": {"blk": [d_, e], "st": "o"}})
    req("PATCH", f"/api/notes/{d_}", body={"props": {"st": "f"}})
    req("PATCH", f"/api/notes/{e}", body={"props": {"st": "l"}})
    ok("Gegenseite: D/E blockieren C", props(d_).get(bing["id"]) == [c_] and props(e).get(bing["id"]) == [c_], (props(d_), props(e)))
    d = rows(db)
    by = {r["title"]: r for r in d["rows"]}
    ok("blocked: C blockiert (E in Arbeit), D nicht", by["C"].get("blocked") is True and "blocked" not in by["D"], (by["C"], by["D"]))
    ok("blocked in GET /api/notes/{zeile}", req("GET", f"/api/notes/{c_}")[1].get("blocked") is True)
    req("PATCH", f"/api/notes/{e}", body={"props": {"st": "f"}})
    d = rows(db)
    ok("alle Abhaengigkeiten erledigt -> nicht mehr blockiert", "blocked" not in {r["title"]: r for r in d["rows"]}["C"])
    s, d = req("PATCH", f"/api/notes/{c_}", body={"props": {"blk": [c_]}})
    ok("blocked_by Selbstbezug -> 400", s == 400, (s, d))
    req("PATCH", f"/api/notes/{c_}", body={"props": {"blk": [e]}})
    s, d = req("PATCH", f"/api/notes/{e}", body={"props": {"blk": [c_]}})
    ok("gegenseitige Abhaengigkeit erlaubt (kein Kreis-Zwang)", s == 200, (s, d))
    # ohne Status: erstes Haekchen zaehlt
    db2 = new_db("Haken", [{"id": "ok", "name": "Erledigt", "type": "checkbox"}])
    sch2 = req("GET", f"/api/notes/{db2}")[1]["schema"]
    sch2["props"].append({"id": "bb", "name": "Blockiert durch", "type": "relation", "target": db2, "two_way": True, "role": "blocked_by"})
    req("PATCH", f"/api/notes/{db2}", body={"schema": sch2})
    x1, x2 = row(db2, "X1"), row(db2, "X2")
    req("PATCH", f"/api/notes/{x2}", body={"props": {"bb": [x1]}})
    ok("ohne Status: offenes Haekchen blockiert", next(r for r in rows(db2)["rows"] if r["title"] == "X2").get("blocked") is True)
    req("PATCH", f"/api/notes/{x1}", body={"props": {"ok": True}})
    ok("Haekchen gesetzt -> frei", "blocked" not in next(r for r in rows(db2)["rows"] if r["title"] == "X2"))

    # Rolle bei Relation auf fremde DB wird verworfen
    sch2 = req("GET", f"/api/notes/{db2}")[1]["schema"]
    sch2["props"].append({"id": "fr", "name": "Fremd", "type": "relation", "target": db, "role": "parent"})
    req("PATCH", f"/api/notes/{db2}", body={"schema": sch2})
    fr = next(x for x in req("GET", f"/api/notes/{db2}")[1]["schema"]["props"] if x["id"] == "fr")
    ok("Rolle nur bei Selbst-Relation", "role" not in fr, fr)

    # Rechte: Bearbeiter darf Unteraufgaben setzen, Leser nicht
    with connect() as c, c:
        c.execute("INSERT INTO note_share (note_id, owner_uid, target_type, target, role, created, created_by) VALUES (?,?,?,?,?,?,?)",
                  (db, A, "user", BU, "edit", time.time(), A))
    s, d = req("PATCH", f"/api/notes/{d_}", u=BU, body={"props": {"par": [a]}})
    ok("geteilter Bearbeiter setzt Elternteil", s == 200 and d_ in props(a).get(kids["id"], []), (s, d, props(a)))
    s, d = req("PATCH", f"/api/notes/{d_}", u=BU, body={"props": {"par": [d_]}})
    ok("Bearbeiter: Selbstbezug ebenfalls 400", s == 400)
    with connect() as c, c:
        c.execute("UPDATE note_share SET role='view' WHERE note_id=? AND target=?", (db, BU))
    s, d = req("PATCH", f"/api/notes/{d_}", u=BU, body={"props": {"par": []}})
    ok("Leser darf nicht", s in (403, 404), (s, d))

    # Entkoppeln
    sch = req("GET", f"/api/notes/{db}")[1]["schema"]
    for x in sch["props"]:
        if x["id"] == "par":
            x["two_way"] = False
    req("PATCH", f"/api/notes/{db}", body={"schema": sch})
    sch = req("GET", f"/api/notes/{db}")[1]["schema"]
    ok("zweiseitig aus: Gegenspalte weg, par bleibt einseitig ohne Rolle",
       not any(x["id"] == kids["id"] for x in sch["props"]) and "pair" not in next(x for x in sch["props"] if x["id"] == "par")
       and "role" not in next(x for x in sch["props"] if x["id"] == "par"), sch["props"])
    ok("Werte der Gegenspalte entfernt", kids["id"] not in props(a), props(a))
    ok("sub-Ansicht faellt weg (Rolle fehlt)", "sub" not in sch["views"][0], sch["views"][0])
    sch["props"] = [x for x in sch["props"] if x["id"] != bing["id"]]
    req("PATCH", f"/api/notes/{db}", body={"schema": sch})
    blk = next(x for x in req("GET", f"/api/notes/{db}")[1]["schema"]["props"] if x["id"] == "blk")
    ok("Gegenspalte geloescht: Spalte entkoppelt", "pair" not in blk and "role" not in blk, blk)


def main():
    from saga.i18n import set_lang
    set_lang("de")
    cleanup()
    prev = auth.settings(fresh=True).get("share_enabled", True)
    auth.save_setting("share_enabled", True)
    for u in (A, BU):
        auth.create_user(u, "Unter-Pass-1234", u.upper())
    time.sleep(5.6)
    e0 = errs()
    try:
        run()
    finally:
        auth.save_setting("share_enabled", prev)
        cleanup()
    n = errs() - e0
    ok(f"Serverfehler: {n}", n == 0)


def cleanup():
    from saga.userdata import delete_user_data
    for u in (A, BU):
        if auth.get_user(u):
            delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM note WHERE uid LIKE 'p5s-%'")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'p5s-%' OR target LIKE 'p5s-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'p5s-%'")


if __name__ == "__main__":
    main()
    print(f"\nUNTERAUFGABEN {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
    sys.exit(0 if res and all(res) else 1)
