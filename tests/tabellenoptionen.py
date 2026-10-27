"""Tabellenoptionen (2.5, Paket 5, tableopts1).

a) Einfache Tabelle (Editor-Block "table"): headCol, colWidths (%, je 5-90, Summe 100, Anzahl = Spalten, <= 20),
   colors {r, c, x} - der Server bereinigt (Typen, Grenzen, Indizes innerhalb der Tabelle, Farben aus der Palette),
   auch verschachtelt (Spalten/Toggle); unbekannte Felder bleiben (Regel 0); Veroeffentlichen zeigt Kopfspalte (th),
   Breiten als Klassen pw-N (CSP ohne Inline-Stil) und Farben pc-<farbe>; Markdown ohne Farben/Breiten.
b) Datenbank-Ansicht: wrap, wrap_cols (nur vorhandene Spalten/title), freeze 1..3, Kalender range "week" (nur Kalender),
   colors (bedingte Farbe) mit den neuen Operatoren; unbekannte Werte fallen weg.
Wegwerf-Konten p5t-*, raeumt auf."""
import base64
import http.client
import json
import os
import secrets
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
A, BU = "p5t-a", "p5t-b"
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


def blocks_of(nid):
    s, g = req("GET", f"/api/notes/{nid}")
    return json.loads(g["body"])["blocks"]


def run():
    s, d = req("POST", "/api/notes", body={"title": "Tabellen"})
    nid = d["id"]
    tbl = {"type": "table", "data": {"withHeadings": True, "content": [["Name", "Wert", "Notiz"], ["a", "1", "x"], ["b", "2", "y"]],
                                     "headCol": True, "colWidths": [50, 30, 20], "zukunft": {"x": 1},
                                     "colors": {"r": {"1": "blue", "7": "red", "x": "red"}, "c": {"2": "green", "3": "red"},
                                                "x": {"2,1": "red", "2,9": "red", "1,1": "lila", "a,b": "red"}}}}
    bad = {"type": "table", "data": {"withHeadings": False, "content": [["1", "2"]], "headCol": "ja", "colWidths": [100],
                                     "colors": "rot"}}
    norm = {"type": "table", "data": {"content": [["1", "2", "3"]], "colWidths": [1, 1, 98]}}
    nested = {"type": "toggle", "data": {"text": "T", "blocks": [json.loads(json.dumps(tbl))]}}
    body = {"blocks": [tbl, bad, norm, nested]}
    s, d = req("PATCH", f"/api/notes/{nid}", body={"body": json.dumps(body), "format": "blocks"})
    ok("Seite mit Tabellen gespeichert", s == 200, d)
    bl = blocks_of(nid)
    t0 = bl[0]["data"]
    ok("headCol true bleibt", t0.get("headCol") is True, t0)
    ok("colWidths gueltig bleibt (Summe 100)", t0.get("colWidths") == [50, 30, 20], t0.get("colWidths"))
    ok("Farben: nur gueltige Indizes/Farben", t0.get("colors") == {"r": {"1": "blue"}, "c": {"2": "green"}, "x": {"2,1": "red"}}, t0.get("colors"))
    ok("unbekanntes Feld bleibt (Regel 0)", t0.get("zukunft") == {"x": 1}, t0)
    t1 = bl[1]["data"]
    ok("falsche Werte fallen weg (headCol, colWidths falsche Anzahl, colors kein Objekt)",
       "headCol" not in t1 and "colWidths" not in t1 and "colors" not in t1 and t1.get("content") == [["1", "2"]], t1)
    t2 = bl[2]["data"]
    ok("colWidths auf 5-90 begrenzt und auf 100 normiert", t2.get("colWidths") and abs(sum(t2["colWidths"]) - 100) < 0.01
       and min(t2["colWidths"]) >= 4.9 and max(t2["colWidths"]) <= 90.1, t2.get("colWidths"))
    t3 = bl[3]["data"]["blocks"][0]["data"]
    ok("verschachtelt (Toggle) ebenso bereinigt", t3.get("colors") == t0.get("colors") and t3.get("headCol") is True, t3)
    big = {"type": "table", "data": {"content": [[str(i) for i in range(21)]], "colWidths": [5] * 21}}
    req("PATCH", f"/api/notes/{nid}", body={"body": json.dumps({"blocks": [big]}), "format": "blocks"})
    ok("mehr als 20 Spalten: keine Breiten", "colWidths" not in blocks_of(nid)[0]["data"])
    req("PATCH", f"/api/notes/{nid}", body={"body": json.dumps(body), "format": "blocks"})

    # Markdown ohne Farben/Breiten
    s, raw = req("GET", f"/api/notes/{nid}/export", raw=True)
    md = raw.decode("utf-8", "replace")
    ok("Markdown: Tabelle ohne Farben/Breiten", s == 200 and "| Name | Wert | Notiz |" in md and "blue" not in md and "colWidths" not in md, md[:400])

    # Veroeffentlichen
    prev = auth.settings(fresh=True).get("publish_enabled", True)
    auth.save_setting("publish_enabled", True)
    pub = "p5t-" + secrets.token_hex(4)
    try:
        s, d = req("PUT", f"/api/notes/{nid}/publish", body={"slug": "tabellen", "name": pub})
        ok("veroeffentlicht", s == 200, d)
        s, html = req("GET", f"/@{pub}/tabellen", u=None, raw=True)
        h = html.decode("utf-8", "replace")
        ok("oeffentlich: Kopfspalte als th", s == 200 and "<th>b</th>" in h and "<th class=\"pc-blue\">a</th>" in h, h[h.find("pub-table"):h.find("pub-table") + 600])
        ok("oeffentlich: Breiten als Klassen pw-50/30/20 (kein Inline-Stil)", 'class="pw-50"' in h and 'class="pw-30"' in h and "style=" not in h[h.find("pub-table"):h.find("</table>")])
        ok("oeffentlich: Farben (Zeile blau, Spalte gruen, Zelle rot)", 'class="pc-blue"' in h and 'class="pc-green"' in h and 'class="pc-red"' in h)
        s, css = req("GET", "/static/public.css", u=None, raw=True)
        ok("public.css hat pw-/pc-Klassen", b".pw-50{width:50%}" in css and b".pc-red{" in css)
    finally:
        req("DELETE", f"/api/notes/{nid}/publish")
        auth.save_setting("publish_enabled", prev)

    # Datenbank-Ansicht
    s, d = req("POST", "/api/notes", body={"kind": "database", "title": "Ansichten"})
    db = d["id"]
    s, g = req("GET", f"/api/notes/{db}")
    sch = g["schema"]
    sch["views"] = [{"id": "t", "name": "T", "type": "table", "wrap": True, "wrap_cols": ["title", "status", "weg", "status"], "freeze": 2},
                    {"id": "u", "name": "U", "type": "table", "wrap": "ja", "freeze": 7, "range": "week"},
                    {"id": "k", "name": "K", "type": "calendar", "date_by": "datum", "range": "week"},
                    {"id": "m", "name": "M", "type": "calendar", "range": "year"},
                    {"id": "b", "name": "B", "type": "board", "group_by": "status",
                     "colors": [{"pid": "datum", "op": "overdue", "value": "", "color": "red"},
                                {"pid": "status", "op": "status_group", "value": "done", "color": "green"},
                                {"pid": "datum", "op": "kaputt", "value": "", "color": "red"}]}]
    s, d = req("PATCH", f"/api/notes/{db}", body={"schema": sch})
    vs = {v["id"]: v for v in req("GET", f"/api/notes/{db}")[1]["schema"]["views"]}
    ok("wrap/wrap_cols/freeze bleiben (nur vorhandene Spalten, ohne Doppelte)",
       vs["t"].get("wrap") is True and vs["t"].get("wrap_cols") == ["title", "status"] and vs["t"].get("freeze") == 2, vs["t"])
    ok("ungueltig faellt weg (wrap nicht true, freeze > 3, range bei Tabelle)",
       "wrap" not in vs["u"] and "freeze" not in vs["u"] and "range" not in vs["u"], vs["u"])
    ok("Kalender range week bleibt, anderes faellt weg", vs["k"].get("range") == "week" and "range" not in vs["m"], (vs["k"], vs["m"]))
    ok("bedingte Farben mit neuen Operatoren (unbekannter faellt weg)",
       [c["op"] for c in vs["b"].get("colors", [])] == ["overdue", "status_group"], vs["b"])


def main():
    from saga.i18n import set_lang
    set_lang("de")
    cleanup()
    for u in (A, BU):
        auth.create_user(u, "Tabelle-Pass-1234", u.upper())
    e0 = errs()
    try:
        run()
    finally:
        cleanup()
    n = errs() - e0
    ok(f"Serverfehler: {n}", n == 0)


def cleanup():
    from saga.userdata import delete_user_data
    for u in (A, BU):
        if auth.get_user(u):
            delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM note WHERE uid LIKE 'p5t-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'p5t-%'")


if __name__ == "__main__":
    main()
    print(f"\nTABELLENOPTIONEN {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
    sys.exit(0 if res and all(res) else 1)
