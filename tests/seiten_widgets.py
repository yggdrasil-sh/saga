"""Widgets in Seiten (2.5, Etappe 1): Bereinigung des Blocks "widget" (bekannte Arten streng, unbekannte unveraendert,
auch in Spalten/Toggles, eindeutige ids), alle Rechenarten (gemeinsame Testvektoren tests/widget_vectors.json, auch fuer
den Browser), Rechte (eigene DB, geteilte Seite mit nicht geteilter DB, Lesen, geloeschte DB, Zugriff entzogen trotz
Zwischenspeicher), too_big, Zitat des Tages deterministisch, Vorschau braucht 'Bearbeiten', Veroeffentlichen (fremde Quelle
entfaellt, keine Inline-Styles), Markdown-Export, Klartext/Suche nur mit Titel. Nicht verwechseln mit tests/widgets.py
(Home-Bildschirm-Widgets der Apps). Wegwerf-Konten sw-a/sw-b/sw-c, raeumt auf. Umgebung wie tests/gestalten.py."""
import base64, http.client, json, os, re, sys, time, urllib.parse
from itsdangerous import TimestampSigner
import auth
from db import connect

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from saga import dbquery, page_widgets  # noqa: E402

B = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
HERE = os.path.dirname(os.path.abspath(__file__))
res = []
n500 = [0]


def ok(n, c, info=None):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, "" if c or info is None else f"-> {info}")


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, cookie=None, body=None):
    c = http.client.HTTPConnection(B.hostname, B.port, timeout=30)
    h = {"Host": HOST, "Accept-Language": "de"}
    if cookie:
        h["Cookie"] = "saga=" + cookie; h["X-Saga"] = "1"
    raw = None
    if body is not None:
        h["Content-Type"] = "application/json"; raw = json.dumps(body)
    c.request(m, p, body=raw, headers=h)
    r = c.getresponse(); data = r.read()
    if r.status >= 500:
        n500[0] += 1
    ct = r.getheader("content-type", "")
    if "json" in ct:
        try:
            data = json.loads(data)
        except ValueError:
            pass
    elif ct.startswith("text/"):
        data = data.decode("utf-8", "replace")
    return r.status, data


def cleanup():
    with connect() as c, c:
        for t in ("note", "note_version", "user_kv", "file", "note_publish", "public_name"):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE 'sw-%'")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'sw-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'sw-%'")


def W(kind, cfg=None, bid=None, **kw):
    b = {"type": "widget", "data": dict({"v": 1, "kind": kind, "cfg": cfg or {}}, **kw)}
    if bid:
        b["id"] = bid
    return b


def save(A, nid, blocks):
    s, d = req("PATCH", f"/api/notes/{nid}", A, {"body": json.dumps({"blocks": blocks}), "format": "blocks"})
    assert s == 200, (s, d)
    return json.loads(req("GET", f"/api/notes/{nid}", A)[1]["body"])["blocks"]


def vectors():
    v = json.load(open(os.path.join(HERE, "widget_vectors.json")))
    bad = []
    for cse in v["cases"]:
        got = page_widgets.aggregate(cse["op"], cse["prop"], v["sets"][cse["set"]], v["schema"])
        if got != cse["want"]:
            bad.append((cse, got))
    ok(f"Testvektoren Aggregation ({len(v['cases'])} Faelle, wie Web-Fusszeile)", not bad, bad[:3])
    # Unbekannte Eigenschaft -> bad_cfg
    try:
        page_widgets.aggregate("sum", "gibtsnicht", [], v["schema"])
        ok("unbekannte Eigenschaft -> bad_cfg", False)
    except page_widgets._Fail as f:
        ok("unbekannte Eigenschaft -> bad_cfg", f.code == "bad_cfg")


def main():
    cleanup()
    vectors()
    for u, n in (("sw-a", "Anna Widget"), ("sw-b", "Bert Widget"), ("sw-c", "Cora Widget")):
        auth.create_user(u, "Widget-Pass-123", n)
    A, Bk, C = ck("sw-a"), ck("sw-b"), ck("sw-c")
    nid = req("POST", "/api/notes", A, {"title": "Cockpit"})[1]["id"]

    # --- Datenbank als Quelle --------------------------------------------------------------------------------------
    dbid = req("POST", "/api/notes", A, {"title": "Aufgaben-DB Geheim", "kind": "database"})[1]["id"]
    schema = {"props": [{"id": "erl", "name": "Erledigt", "type": "checkbox"}, {"id": "std", "name": "Stunden", "type": "number"},
                        {"id": "sp", "name": "Spruch", "type": "text"}],
              "views": [{"id": "alle", "name": "Alle", "type": "table"},
                        {"id": "offen", "name": "Offen", "type": "table", "filters": [{"pid": "erl", "op": "unchecked"}]}]}
    s, _ = req("PATCH", f"/api/notes/{dbid}", A, {"schema": schema})
    rows = [("Eins", True, 2, "Carpe diem"), ("Zwei", False, 3.5, "Festina lente"), ("Drei", True, None, ""), ("Vier", False, None, "Memento")]
    for ti, e, h, sp in rows:
        props = {"erl": e, "sp": sp}
        if h is not None:
            props["std"] = h
        req("POST", "/api/notes", A, {"title": ti, "parent_id": dbid, "props": props})
    ok(f"Quell-Datenbank mit 4 Zeilen ({s})", s == 200)

    # --- Bereinigung --------------------------------------------------------------------------------------------------
    got = save(A, nid, [
        W("metric", {"db": dbid, "view": "alle", "agg": "count", "prop": "std", "fmt": "num", "bogus": 1}, bid="m1",
          size="xl", style="card", accent="lila", title="<b>Offene</b> Aufgaben" + "x" * 100, extra="weg"),
        W("progress", {"src": "fixed", "value": "7", "max": -3}, bid="p1", accent="green"),
        W("clock", {"tz": "Mars/Olymp", "h24": False, "sec": 1}, bid="c1"),
        W("countdown", {"date": "2026-02-30", "time": "25:00", "dir": "since", "unit": "x"}, bid="d1"),
        W("quote", {"src": "fixed", "items": ["<i>Eins</i>", 5, "", "y" * 400] + ["z"] * 30, "pick": "seltsam"}, bid="q1"),
        {"type": "widget", "id": "f1", "data": {"v": 2, "kind": "chart", "cfg": {"neu": [1, 2]}, "zukunft": True}},
        {"type": "widget", "id": "m1", "data": {"kind": "metric", "cfg": {"db": -5, "agg": "avg", "goal": "10"}}},
        {"type": "columns", "data": {"cols": [{"blocks": [W("metric", {"db": dbid, "agg": "sum", "prop": "std", "fmt": "eur"}, bid="m1")]},
                                              {"blocks": [{"type": "toggle", "data": {"title": "T", "blocks": [W("progress", {"src": "db", "db": dbid, "mode": "checked", "prop": "erl"}, bid="p2")]}}]}]}},
    ])
    m1, p1, c1, d1, q1, f1, mx = got[:7]
    ok("metric: size/accent ungueltig weg, Titel Klartext 80, cfg ohne Unbekanntes, count ohne prop",
       "size" not in m1["data"] and "accent" not in m1["data"] and m1["data"]["style"] == "card" and "extra" not in m1["data"]
       and m1["data"]["title"] == ("Offene Aufgaben" + "x" * 100)[:80]
       and m1["data"]["cfg"] == {"db": dbid, "view": "alle", "agg": "count", "prop": "", "fmt": "num"}, m1)
    ok("progress fixed: value aus Text -> 0, max <= 0 -> 100, accent green", p1["data"]["cfg"] == {"src": "fixed", "value": 0, "max": 100}
       and p1["data"]["accent"] == "green", p1)
    ok("clock: unbekannte Zeitzone -> '', Wahrheitswerte streng", c1["data"]["cfg"] == {"tz": "", "h24": False, "sec": False, "analog": False}, c1)
    ok("countdown: falsches Datum/Uhrzeit weg, dir since, unit days", d1["data"]["cfg"] == {"date": "", "time": "", "dir": "since", "unit": "days"}, d1)
    qi = q1["data"]["cfg"]["items"]
    ok("quote: Texte Klartext, leere weg, je 300, hoechstens 20, pick daily", qi[0] == "Eins" and len(qi) == 20 and len(qi[1]) == 300
       and q1["data"]["cfg"]["pick"] == "daily", qi[:3])
    ok("unbekannte Art bleibt unveraendert (Vorwaertskompatibilitaet)", f1["data"] == {"v": 2, "kind": "chart", "cfg": {"neu": [1, 2]}, "zukunft": True}, f1)
    inner_m = got[7]["data"]["cols"][0]["blocks"][0]
    inner_p = got[7]["data"]["cols"][1]["blocks"][0]["data"]["blocks"][0]
    ids = [b.get("id") for b in (m1, p1, c1, d1, q1, f1, mx, inner_m, inner_p)]
    ok("doppelte ids (auch in Spalten/Toggles) werden neu vergeben", len(set(ids)) == len(ids) and ids[0] == "m1" and inner_p["id"] == "p2", ids)
    ok("fremde DB-ID (negativ) -> db null, goal aus Text weg", mx["data"]["cfg"]["db"] is None and "goal" not in mx["data"]["cfg"], mx)
    ok("in Spalten bereinigt (sum mit prop, eur)", inner_m["data"]["cfg"] == {"db": dbid, "view": "", "agg": "sum", "prop": "std", "fmt": "eur"}, inner_m)

    # --- Rechnen -------------------------------------------------------------------------------------------------------
    blocks = [
        W("metric", {"db": dbid, "view": "alle", "agg": "count"}, bid="cnt", title="Alle"),
        W("metric", {"db": dbid, "view": "offen", "agg": "count"}, bid="open", title="Offen"),
        W("metric", {"db": dbid, "agg": "sum", "prop": "std", "fmt": "num", "goal": 10}, bid="sum"),
        W("metric", {"db": dbid, "agg": "avg", "prop": "std"}, bid="avg"),
        W("metric", {"db": dbid, "agg": "min", "prop": "std"}, bid="min"),
        W("metric", {"db": dbid, "agg": "max", "prop": "std"}, bid="max"),
        W("metric", {"db": dbid, "agg": "pct_checked", "prop": "erl"}, bid="pct"),
        W("metric", {"db": dbid, "agg": "sum", "prop": "nix"}, bid="bad"),
        W("progress", {"src": "db", "db": dbid, "mode": "checked", "prop": "erl"}, bid="pc"),
        W("progress", {"src": "db", "db": dbid, "mode": "sum", "prop": "std", "goal": 10}, bid="ps"),
        W("progress", {"src": "fixed", "value": 3, "max": 4}, bid="pf"),
        W("quote", {"src": "db", "db": dbid, "prop": "sp", "pick": "daily"}, bid="qd"),
        W("quote", {"src": "fixed", "items": ["A", "B", "C", "D", "E"], "pick": "daily"}, bid="qf"),
        W("clock", {}, bid="clk"),
        {"type": "columns", "data": {"cols": [{"blocks": [W("metric", {"db": dbid, "agg": "count"}, bid="incol")]}, {"blocks": []}]}},
    ]
    save(A, nid, blocks)
    s, d = req("GET", f"/api/notes/{nid}/widgets?tz=Europe/Berlin", A)
    w = d.get("widgets", {}) if isinstance(d, dict) else {}
    ok(f"GET widgets ({s})", s == 200 and set(w) == {"cnt", "open", "sum", "avg", "min", "max", "pct", "bad", "pc", "ps", "qd", "qf", "incol"}, sorted(w))
    keys = {"kind", "value", "max", "text", "fmt", "as_of", "error"}
    ok("Antwortformat W (genau die Felder)", all(set(x) == keys for x in w.values()), list(w.values())[:1])
    ok("count (Ansicht alle) = 4, (Ansicht offen) = 2", w["cnt"]["value"] == 4 and w["open"]["value"] == 2, (w["cnt"], w["open"]))
    ok("sum = 5.5 (leere Werte zaehlen nicht), goal als max, fmt", w["sum"]["value"] == 5.5 and w["sum"]["max"] == 10 and w["sum"]["fmt"] == "num", w["sum"])
    ok("avg/min/max", w["avg"]["value"] == 2.75 and w["min"]["value"] == 2 and w["max"]["value"] == 3.5, (w["avg"], w["min"], w["max"]))
    ok("pct_checked = 50, fmt pct", w["pct"]["value"] == 50 and w["pct"]["fmt"] == "pct", w["pct"])
    ok("unbekannte Eigenschaft -> bad_cfg ohne Wert", w["bad"]["error"] == "bad_cfg" and w["bad"]["value"] is None, w["bad"])
    ok("progress checked = 2 von 4", w["pc"]["value"] == 2 and w["pc"]["max"] == 4, w["pc"])
    ok("progress sum = 5.5 von 10", w["ps"]["value"] == 5.5 and w["ps"]["max"] == 10, w["ps"])
    ok("Widget in Spalte gerechnet", w["incol"]["value"] == 4, w["incol"])
    ok("as_of ISO-Zeit", re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", w["cnt"]["as_of"] or ""), w["cnt"]["as_of"])
    # Zitat des Tages: deterministisch aus (Tag, Block-ID)
    from datetime import date
    import hashlib
    items = ["A", "B", "C", "D", "E"]
    h = hashlib.sha256(f"{date.today().isoformat()}|qf".encode()).digest()
    want = items[int.from_bytes(h[:8], "big") % 5]
    s2, d2 = req("GET", f"/api/notes/{nid}/widgets", A)
    ok("Zitat daily deterministisch (Tag + Block-ID)", w["qf"]["text"] == want and d2["widgets"]["qf"]["text"] == want, (w["qf"], want))
    ok("Zitat aus DB: Text einer Zeile mit Inhalt", w["qd"]["text"] in ("Carpe diem", "Festina lente", "Memento"), w["qd"])
    with connect() as c:
        wd = page_widgets.compute(c, "sw-a", "qx", page_widgets.clean_data({"kind": "quote", "cfg": {"items": items, "pick": "daily"}}),
                                  today=date(2026, 1, 1))
        wd2 = page_widgets.compute(c, "sw-a", "qx", page_widgets.clean_data({"kind": "quote", "cfg": {"items": items, "pick": "daily"}}),
                                   today=date(2026, 1, 1))
    ok("Zitat daily: gleicher Tag/Block -> gleicher Text", wd["text"] == wd2["text"] and wd["text"] in items, (wd, wd2))
    s, _ = req("GET", f"/api/notes/{nid}/widgets?tz=Mond/Basis", A)
    ok(f"ungueltige Zeitzone -> 400 ({s})", s == 400)

    # --- Vorschau -------------------------------------------------------------------------------------------------------
    pv = {"block": W("metric", {"db": dbid, "agg": "count"}, bid="neu")}
    s, d = req("POST", f"/api/notes/{nid}/widgets/preview", A, pv)
    ok(f"Vorschau (Eigentuemer) ({s})", s == 200 and d["widget"]["value"] == 4, d)
    s, d = req("POST", f"/api/notes/{nid}/widgets/preview", A, {"block": {"type": "paragraph", "data": {}}})
    ok(f"Vorschau: kein Widget -> 400 ({s})", s == 400)

    # --- Rechte -------------------------------------------------------------------------------------------------------
    s, _ = req("GET", f"/api/notes/{nid}/widgets", Bk)
    ok(f"fremde Seite ohne Freigabe -> 404 ({s})", s == 404)
    s, _ = req("PUT", f"/api/notes/{nid}/shares", A, {"type": "user", "target": "sw-b", "role": "view"})
    s, d = req("GET", f"/api/notes/{nid}/widgets", Bk)
    raw = json.dumps(d, ensure_ascii=False)
    ok(f"geteilte Seite, DB nicht geteilt -> no_access, keine Werte ({s})", s == 200 and d["widgets"]["cnt"]["error"] == "no_access"
       and all(x["value"] is None and x["text"] is None for k, x in d["widgets"].items() if k != "qf"), d)
    ok("kein Name/Wert der DB in der Antwort", "Geheim" not in raw and "Carpe" not in raw and "Festina" not in raw and "Memento" not in raw, raw[:200])
    ok("feste Zitate auch fuer Lesende", d["widgets"]["qf"]["text"] == want)
    s, d = req("POST", f"/api/notes/{nid}/widgets/preview", Bk, pv)
    ok(f"Vorschau mit 'Lesen' -> 403 ({s})", s == 403)
    s, _ = req("PUT", f"/api/notes/{nid}/shares", A, {"type": "user", "target": "sw-b", "role": "edit"})
    s, d = req("POST", f"/api/notes/{nid}/widgets/preview", Bk, pv)
    ok(f"Vorschau mit 'Bearbeiten', DB ohne Zugriff -> no_access ({s})", s == 200 and d["widget"]["error"] == "no_access"
       and "Geheim" not in json.dumps(d, ensure_ascii=False), d)
    s, _ = req("PUT", f"/api/notes/{dbid}/shares", A, {"type": "user", "target": "sw-b", "role": "view"})
    s, d = req("GET", f"/api/notes/{nid}/widgets", Bk)
    ok(f"DB mit Lesen geteilt -> Werte ({s})", s == 200 and d["widgets"]["cnt"]["value"] == 4 and d["widgets"]["pc"]["value"] == 2, d["widgets"].get("cnt"))
    # Archivierte Zeile: fuer Fremde unsichtbar (wie die Web-Ansicht einer geteilten Datenbank)
    rid4 = [r["id"] for r in req("GET", f"/api/notes/{dbid}/rows", A)[1]["rows"] if r["title"] == "Vier"][0]
    req("POST", f"/api/notes/{rid4}/archive", A, {"archived": True})
    time.sleep(0.05)
    with connect() as c:
        wa = page_widgets.compute(c, "sw-a", "x", page_widgets.clean_data({"kind": "metric", "cfg": {"db": dbid, "agg": "count"}}))
        wb = page_widgets.compute(c, "sw-b", "x", page_widgets.clean_data({"kind": "metric", "cfg": {"db": dbid, "agg": "count"}}))
    ok("archivierte Zeile: Eigentuemer 4, Geteilt 3 (wie die Ansicht)", wa["value"] == 4 and wb["value"] == 3, (wa, wb))
    req("POST", f"/api/notes/{rid4}/archive", A, {"archived": False})
    # Freigabe der DB wieder entzogen: trotz Zwischenspeicher sofort no_access
    req("DELETE", f"/api/notes/{dbid}/shares?type=user&target=sw-b", A)
    s, d = req("GET", f"/api/notes/{nid}/widgets", Bk)
    ok(f"Freigabe entzogen -> sofort no_access (Speicher umgangen) ({s})", s == 200 and d["widgets"]["cnt"]["error"] == "no_access"
       and d["widgets"]["cnt"]["value"] is None, d["widgets"].get("cnt"))
    # Cora: eigene Seite mit Widget auf Annas DB (ID geraten) -> no_access, nichts verraten
    cn = req("POST", "/api/notes", C, {"title": "Coras Seite"})[1]["id"]
    save(C, cn, [W("metric", {"db": dbid, "agg": "count"}, bid="spy"), W("metric", {"db": 999999999, "agg": "count"}, bid="none")])
    s, d = req("GET", f"/api/notes/{cn}/widgets", C)
    ok(f"fremde DB-ID auf eigener Seite -> no_access, nicht vorhandene ebenso ({s})", s == 200 and d["widgets"]["spy"]["error"] == "no_access"
       and d["widgets"]["none"]["error"] == "no_access", d)

    # --- too_big und gone ----------------------------------------------------------------------------------------------
    big = req("POST", "/api/notes", A, {"title": "Gross", "kind": "database"})[1]["id"]
    with connect() as c, c:
        now = time.time()
        for i in range(dbquery.LIMIT_MAX + 1):
            c.execute("INSERT INTO note (uid, title, body, format, parent_id, created, updated) VALUES (?, ?, '', 'blocks', ?, ?, ?)",
                      ("sw-a", f"z{i}", big, now, now))
    gn = req("POST", "/api/notes", A, {"title": "Grenzen"})[1]["id"]
    save(A, gn, [W("metric", {"db": big, "agg": "count"}, bid="big"), W("metric", {"db": dbid, "agg": "count"}, bid="gone")])
    s, d = req("GET", f"/api/notes/{gn}/widgets", A)
    ok(f"mehr als LIMIT_MAX Zeilen -> too_big ({s})", s == 200 and d["widgets"]["big"]["error"] == "too_big" and d["widgets"]["big"]["value"] is None, d["widgets"].get("big"))

    # --- Klartext / Suche --------------------------------------------------------------------------------------------
    with connect() as c:
        txt = c.execute("SELECT body FROM note WHERE id=?", (nid,)).fetchone()[0]
    from saga.notes import blocks_text
    bt = blocks_text(json.loads(txt)["blocks"])
    ok("Klartext/Suche: nur Titel, keine technischen Werte", "Alle" in bt and "Offen" in bt and "metric" not in bt and "pct_checked" not in bt
       and "daily" not in bt, bt)

    # --- Markdown ------------------------------------------------------------------------------------------------------
    s, md = req("GET", f"/api/notes/{nid}/export", A)
    ok(f"Markdown: **Titel:** Wert, Stand-Datum bei DB-Werten ({s})", s == 200 and "**Alle:** 4 (Stand " in md and "**Kennzahl:** 5.5 / 10" in md
       and "**Fortschritt:** 3 / 4 (75 %)" in md and f"**Zitat:** {want}" in md, md[:600])
    req("PUT", f"/api/notes/{nid}/shares", A, {"type": "user", "target": "sw-b", "role": "view"})
    s, mdb = req("GET", f"/api/notes/{nid}/export", Bk)
    ok(f"Markdown fuer Lesende ohne DB-Zugriff: kein Wert ({s})", s == 200 and "**Alle:** –" in mdb and "Carpe" not in mdb and "Geheim" not in mdb, mdb[:400])

    # --- Veroeffentlichen ----------------------------------------------------------------------------------------------
    req("PUT", "/api/account/public-name", A, {"name": "sw-anna"})
    cdb = req("POST", "/api/notes", C, {"title": "Coras DB", "kind": "database"})[1]["id"]
    req("POST", "/api/notes", C, {"title": "c1", "parent_id": cdb})
    req("PUT", f"/api/notes/{cdb}/shares", C, {"type": "user", "target": "sw-a", "role": "view"})
    pub = req("POST", "/api/notes", A, {"title": "Oeffentlich"})[1]["id"]
    save(A, pub, [
        W("metric", {"db": dbid, "agg": "count"}, bid="pm", title="Eintraege", accent="blue", size="l"),
        W("metric", {"db": cdb, "agg": "count"}, bid="pc", title="FremdeQuelle"),
        W("progress", {"src": "fixed", "value": 1, "max": 3}, bid="pp", title="Balken"),
        W("countdown", {"date": "2099-01-01", "dir": "until"}, bid="pd", title="Ziel"),
        W("clock", {}, bid="pk", title="Uhrzeit"),
        W("quote", {"src": "fixed", "items": ["Nur 3<4 dies"]}, bid="pq"),
        {"type": "widget", "id": "pz", "data": {"kind": "zukunft", "title": "Unbekannt"}},
    ])
    s, d = req("PUT", f"/api/notes/{pub}/publish", A, {"slug": "widgets"})
    s, html = req("GET", "/@sw-anna/widgets")
    ok(f"Veroeffentlicht ({s})", s == 200 and "pub-wg" in html, str(html)[:200])
    ok("eigene DB: Wert sichtbar, Akzent/Groesse als Klassen", re.search(r'class="pub-wg wg-l wg-card wa-blue"', html) and ">Eintraege<" in html and ">4<" in html)
    ok("fremde DB (Cora) entfaellt ganz", "FremdeQuelle" not in html)
    ok("Fortschritt als Balken pb-35 (33 % -> 5er-Schritt)", 'class="pb-35"' in html and ">Balken<" in html)
    ok("Countdown Tagesstand, Uhr = Datum", re.search(r"noch \d+ Tage", html) and ">Uhrzeit<" in html)
    ok("Zitat escaped, unbekannte Art weg", "Nur 3&lt;4 dies" in html and "Unbekannt" not in html)
    body = html.split("<body", 1)[-1]
    ok("keine Inline-Styles im Inhalt", " style=" not in body)
    req("DELETE", f"/api/notes/{pub}/publish", A)

    # --- gelöschte Quelle ------------------------------------------------------------------------------------------------
    req("DELETE", f"/api/notes/{dbid}", A)
    s, d = req("GET", f"/api/notes/{gn}/widgets", A)
    ok(f"Quell-DB im Papierkorb -> gone ({s})", s == 200 and d["widgets"]["gone"]["error"] == "gone", d["widgets"].get("gone"))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        res.append(False)
    finally:
        cleanup()
    ok(f"Serverfehler: {n500[0]}", n500[0] == 0)
    print(f"\nSEITEN-WIDGETS {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)
