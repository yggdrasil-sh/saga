"""Inline-HTML in Bloecken: @-Datum (<time>) bleibt erhalten, alles Gefaehrliche faellt weg (Probe-Instanz)."""
import json, os, base64, sqlite3, sys, urllib.request, urllib.parse
from itsdangerous import TimestampSigner
B = os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8080"); DB = os.environ.get("HK_TEST_DB", "/data/saga.db")
S = TimestampSigner(os.environ["SESSION_SECRET"]); A = S.sign(base64.b64encode(json.dumps({"uid": "hk-test-a"}).encode())).decode()
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
def call(m, p, b=None):
    h = {"Host": HOST, "Content-Type": "application/json", "Cookie": "saga=" + A, "X-Saga": "1"}
    try:
        with urllib.request.urlopen(urllib.request.Request(B + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers=h), timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, {}
res = []
def ok(n, c): res.append(bool(c)); print(("OK  " if c else "FAIL"), n)
CASES = [
    ("Datum bleibt", 'Termin <time datetime="2026-10-10" class="hk-date">Sa., 10. Okt.</time>',
     'Termin <time datetime="2026-10-10" class="hk-date">Sa., 10. Okt.</time>'),
    ("Erinnerung mit Uhrzeit bleibt", '<time datetime="2026-10-16T09:00" class="hk-remind">⏰ Fr.</time>',
     '<time datetime="2026-10-16T09:00" class="hk-remind">⏰ Fr.</time>'),
    ("Ungueltiges datetime -> nur Text", '<time datetime="javascript:alert(1)" class="hk-date">x</time>', 'x'),
    ("onclick + fremde Klasse fallen weg", '<time datetime="2026-10-10" class="hk-date evil" onclick="alert(1)">y</time>',
     '<time datetime="2026-10-10" class="hk-date">y</time>'),
    ("time ohne datetime -> nur Text", '<time>z</time>', 'z'),
]
try:
    c, d = call("POST", "/api/notes", {"title": "Inline-Test"}); nid = d.get("id")
    ok("Seite angelegt", c == 200 and nid)
    blocks = [{"id": f"b{i}", "type": "paragraph", "data": {"text": x[1]}} for i, x in enumerate(CASES)]
    blocks.append({"id": "h4", "type": "header", "data": {"text": "Ebene 4", "level": 4}})
    c, _ = call("PATCH", f"/api/notes/{nid}", {"body": json.dumps({"blocks": blocks}), "format": "blocks"})
    ok("Gespeichert", c == 200)
    got = json.loads(call("GET", f"/api/notes/{nid}")[1]["body"])["blocks"]
    for i, (name, _, want) in enumerate(CASES):
        ok(f"{name}: {got[i]['data']['text']!r}", got[i]["data"]["text"] == want)
    ok("Ueberschrift 4 bleibt Ebene 4", got[-1]["data"].get("level") == 4)
    # Seite sperren: Inhalt/Titel unveraenderlich, bis entsperrt
    c, _ = call("PATCH", f"/api/notes/{nid}", {"locked": True})
    ok("Gesperrt (Liste + Seite wissen es)", c == 200 and call("GET", f"/api/notes/{nid}")[1].get("locked") is True
       and {p["id"]: p for p in call("GET", "/api/notes")[1]}[nid].get("locked") is True)
    ok("Gesperrt: Inhalt abgelehnt (423)", call("PATCH", f"/api/notes/{nid}", {"body": json.dumps({"blocks": []}), "format": "blocks"})[0] == 423)
    ok("Gesperrt: Titel abgelehnt (423)", call("PATCH", f"/api/notes/{nid}", {"title": "anders"})[0] == 423)
    ok("Gesperrt: Inhalt unveraendert", len(json.loads(call("GET", f"/api/notes/{nid}")[1]["body"])["blocks"]) == len(blocks))
    ok("Gesperrt: Favorit setzen geht", call("PATCH", f"/api/notes/{nid}", {"pinned": True})[0] == 200)
    ok("Entsperren + speichern in einem", call("PATCH", f"/api/notes/{nid}", {"locked": False, "title": "Neu"})[0] == 200 and call("GET", f"/api/notes/{nid}")[1]["title"] == "Neu")
    # Datenbank-Ansicht: Berechnung je Spalte (nur bekannte Spalten + erlaubte Arten)
    db = call("POST", "/api/notes", {"kind": "database", "title": "Ausgaben"})[1]["id"]
    sch = call("GET", f"/api/notes/{db}")[1]["schema"]
    sch["props"] = [{"id": "num", "name": "Betrag", "type": "number"}]
    sch["views"] = [{"id": "t", "name": "Tabelle", "type": "table", "calcs": {"num": "sum", "title": "count", "weg": "sum", "num2": "drop", "title2": "x"}}]
    c, _ = call("PATCH", f"/api/notes/{db}", {"schema": sch})
    v = call("GET", f"/api/notes/{db}")[1]["schema"]["views"][0]
    ok(f"Berechnungen gespeichert, Unbekanntes verworfen: {v.get('calcs')}", c == 200 and v.get("calcs") == {"num": "sum", "title": "count"})
    # "Berechnet": Formel aus zwei Zahlen-Spalten, Wert nie gespeichert
    sch["props"] = [{"id": "num", "name": "Betrag", "type": "number"}, {"id": "pg", "name": "Seiten", "type": "number"},
                    {"id": "tx", "name": "Text", "type": "text"},
                    {"id": "fo", "name": "Fortschritt", "type": "calc", "op": "pct", "a": "num", "b": "pg", "fmt": "percent", "show": "ring"},
                    {"id": "f2", "name": "Kaputt", "type": "calc", "op": "rm", "a": "tx", "b": "weg"}]
    c, _ = call("PATCH", f"/api/notes/{db}", {"schema": sch})
    ps = {p["id"]: p for p in call("GET", f"/api/notes/{db}")[1]["schema"]["props"]}
    ok(f"Berechnet gespeichert: {ps.get('fo')}", c == 200 and ps["fo"] == {"id": "fo", "name": "Fortschritt", "type": "calc", "op": "pct", "a": "num", "b": "pg", "fmt": "percent", "show": "ring"})
    ok(f"Ungueltige Rechnung bereinigt: {ps.get('f2')}", ps["f2"].get("op") == "div" and "a" not in ps["f2"] and "b" not in ps["f2"])
    row = call("POST", "/api/notes", {"parent_id": db, "title": "Buch", "props": {"num": 66, "pg": 300, "fo": 999}})[1]["id"]
    rp = call("GET", f"/api/notes/{row}")[1]["props"]
    ok(f"Wert nie gespeichert ({rp})", rp.get("num") == 66 and "fo" not in rp)
    sch["props"] = [x for x in sch["props"] if x["id"] != "f2"]
    sch["views"][0]["calcs"] = {"num": "rm -rf"}
    call("PATCH", f"/api/notes/{db}", {"schema": sch})
    ok("Ungueltige Art -> keine Berechnung", "calcs" not in call("GET", f"/api/notes/{db}")[1]["schema"]["views"][0])
finally:
    cn = sqlite3.connect(DB)
    for q in ("DELETE FROM note WHERE uid LIKE ?",):
        try: cn.execute(q, ("hk-test-%",))
        except sqlite3.OperationalError: pass
    cn.commit()
print(f"\nINLINE {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER"); sys.exit(0 if res and all(res) else 1)
