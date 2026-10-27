"""Fix-Runde 27.10. (Galerie-Testrunde), Server-Teile der Web-Funde: #15 Profilbild ohne Bild mit ?q=1 -> 204, #10 Zahlenformat (i18n.fmt_num: Prozent ganzzahlig,
Euro mit 2 Nachkommastellen in der Sprache; Fortschritt-Widget meldet das Format der Spalte; veroeffentlichte Seite zeigt
Tabelle und Widgets so). Konto hk-test-a (oeffentlicher Name fx27-a), raeumt seine Seiten auf.
Umgebung wie die anderen Tests (HK_TEST_BASE, BASE_URL, SESSION_SECRET)."""
import base64, http.client, json, os, time, urllib.parse
from itsdangerous import TimestampSigner
from saga import i18n

B = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
res = []


def ok(n, c, info=None):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, "" if c or info is None else f"-> {info!r}"[:400])


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


A = ck("hk-test-a")


def req(m, p, cookie=None, body=None, lang="de"):
    c = http.client.HTTPConnection(B.hostname, B.port, timeout=30)
    h = {"Host": HOST, "Accept-Language": lang}
    if cookie:
        h["Cookie"] = "saga=" + cookie; h["X-Saga"] = "1"
    raw = None
    if body is not None:
        h["Content-Type"] = "application/json"; raw = json.dumps(body)
    c.request(m, p, body=raw, headers=h)
    r = c.getresponse(); data = r.read()
    ct = r.getheader("content-type", "")
    if "json" in ct:
        data = json.loads(data or b"null")
    elif ct.startswith("text/"):
        data = data.decode("utf-8", "replace")
    return r.status, data


def main():
    # --- i18n.fmt_num: wie Intl.NumberFormat im Browser ------------------------------------------------------------
    nb = " "
    want = {"de": (f"1.430,50{nb}€", f"41{nb}%", "1.430,5", "12.345,68"), "en": ("€1,430.50", "41%", "1,430.5", "12,345.68"),
            "es": (f"1430,50{nb}€", f"41{nb}%", "1430,5", "12.345,68"), "ru": (f"1{nb}430,50{nb}€", f"41{nb}%", f"1{nb}430,5", f"12{nb}345,68"),
            "th": ("€1,430.50", "41%", "1,430.5", "12,345.68")}
    for lang, w in want.items():
        got = (i18n.fmt_num(1430.5, "eur", lang), i18n.fmt_num(41.04, "pct", lang), i18n.fmt_num(1430.5, None, lang), i18n.fmt_num(12345.678, None, lang))
        ok(f"#10 fmt_num {lang}: {got}", got == w, w)
    ok("#10 fmt_num: Euro ganzzahlig mit ,00 / Prozent > 100 / leer",
       i18n.fmt_num(5, "euro", "de") == f"5,00{nb}€" and i18n.fmt_num(119.4, "percent", "de") == f"119{nb}%" and i18n.fmt_num(None) == "–"
       and i18n.fmt_num(-0.001, None, "de") == "0" and i18n.fmt_num(-2.5, None, "de") == "-2,5")

    # --- Datenbank + Seite mit Widgets, Widget-Werte und veroeffentlichte Seite ------------------------------------
    s, d = req("POST", "/api/notes", A, {"kind": "database", "title": "Fix27 Format"})
    D = d["id"]
    sc = req("GET", f"/api/notes/{D}", A)[1]["schema"]
    sc["props"] += [{"id": "eur", "name": "Betrag", "type": "number", "fmt": "euro"}, {"id": "pc", "name": "Anteil", "type": "number", "fmt": "percent"}]
    sc["views"] = [{"id": "tab", "name": "Tabelle", "type": "table"}]
    req("PATCH", f"/api/notes/{D}", A, {"schema": sc})
    req("POST", "/api/notes", A, {"parent_id": D, "title": "A", "props": {"eur": 1000.5, "pc": 41.04}})
    req("POST", "/api/notes", A, {"parent_id": D, "title": "B", "props": {"eur": 430}})
    s, d = req("POST", "/api/notes", A, {"title": "Fix27 Format Seite"})
    P = d["id"]
    bl = [{"id": "wpr", "type": "widget", "data": {"v": 1, "kind": "progress", "title": "Budget",
                                                    "cfg": {"src": "db", "db": D, "view": "", "mode": "sum", "prop": "eur", "goal": 1200}}},
          {"id": "wck", "type": "widget", "data": {"v": 1, "kind": "progress", "title": "Haken", "cfg": {"src": "db", "db": D, "view": "", "mode": "checked", "prop": "pc"}}},
          {"id": "dbb", "type": "database", "data": {"db": D, "view": "tab"}}]
    req("PATCH", f"/api/notes/{P}", A, {"format": "blocks", "body": json.dumps({"blocks": bl})})
    s, d = req("GET", f"/api/notes/{P}/widgets", A)
    wv = (d.get("widgets") or d) if isinstance(d, dict) else {}
    wpr = wv.get("wpr") or {}
    ok(f"#10 Fortschritt (Summe Euro) meldet fmt=eur ({s}, {wpr})", s == 200 and wpr.get("fmt") == "eur" and wpr.get("value") == 1430.5 and wpr.get("max") == 1200)
    ok("#10 Fortschritt (Anteil) ohne fmt", (wv.get("wck") or {}).get("fmt") is None)

    s, _ = req("PUT", "/api/account/public-name", A, {"name": "fx27-a"})
    s, d = req("PUT", f"/api/notes/{P}/publish", A, {})
    url = urllib.parse.urlparse(d.get("url") or "").path
    ok(f"veroeffentlicht ({s} {url})", s == 200 and url)
    s, html = req("GET", url, lang="de")
    ok(f"#10 oeffentlich de: Prozent ganzzahlig, Euro ,00 ({s})", s == 200 and f"41{nb}%" in html and f"1.000,50{nb}€" in html
       and f"430,00{nb}€" in html and "41.04" not in html, html[:300] if isinstance(html, str) else html)
    ok("#10 oeffentlich de: Fortschritt ueber 100 % echt, in Euro", f"1.430,50{nb}€ / 1.200,00{nb}€ (119{nb}%)" in html)
    s, html = req("GET", url, lang="en")
    ok("#10 oeffentlich en: Schreibweise der Sprache", "41%" in html and "€1,000.50" in html and "€1,430.50 / €1,200.00 (119%)" in html)

    # --- #15: Profilbild ohne Bild: Web (?q=1) bekommt 204 statt 404, Apps (ohne q) weiter 404 ---------------------------
    s1, _ = req("GET", "/api/avatar/x.unbekannt?s=64", A)
    s2, _ = req("GET", "/api/avatar/x.unbekannt?s=64&q=1", A)
    s3, _ = req("GET", "/api/avatar/x.unbekannt?s=64&q=1")
    ok(f"#15 Profilbild ohne Bild: ohne q {s1}, mit q {s2}, ohne Anmeldung {s3}", s1 == 404 and s2 == 204 and s3 == 401)

    req("DELETE", f"/api/notes/{P}/publish", A)
    for i in (P, D):
        req("DELETE", f"/api/notes/{i}", A)
    bad = res.count(False)
    print(f"{bad} von {len(res)} FEHLGESCHLAGEN" if bad else f"ALLES GRUEN ({len(res)})")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
