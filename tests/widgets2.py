"""Widgets Etappe 2 + Diagramme (2.5, Paket 4): Wetter, Gewohnheiten, Anstehendes, Diagramm-Widget und Diagramm-Ansicht.

Nie echtes Netz: Open-Meteo und CalDAV sind lokale Fake-Server auf 127.0.0.1 (Ports WX2_PORTS="wetter,caldav", Standard
18291,18292; freigegeben ueber allowed_hosts). Geprueft:
- Bereinigung (Grenzen, Koordinaten auf 2 Stellen, unbekannte Felder weg), /api/version features.
- Wetter: Schalter aus -> disabled (auch Ortssuche), Abruf nur durch den Server, Zwischenspeicher (ein Abruf fuer viele
  Anfragen, gleichzeitige zusammengefasst), Stale bei Ausfall (Server mit SAGA_WEATHER_FRESH_S=3 starten - sonst wird
  der Teil uebersprungen), SSRF (private Adresse ohne Freigabe, Weiterleitung, http ohne Freigabe, RESTORE_TEST, zu grosse
  Antwort), Ortssuche (Treffer, Ratenbegrenzung 30/min), Quellenangabe beim Veroeffentlichen.
- Gewohnheiten: Raster, Serie, Quote, Abhaken (Zeile anlegen/setzen/entfernen), Rechte (Lesen an der Quelle reicht nicht),
  Tag-Grenzen, falsche Gewohnheit.
- Anstehendes: Termine (Fake-CalDAV), Aufgaben, Datenbank-Zeilen, ueberfaellig, Limit - Leck-Test: zwei Konten auf derselben
  geteilten Seite sehen jeweils NUR ihre eigenen Termine/Aufgaben (auch nach wiederholtem Abruf = kein Zwischenspeicher-Leck);
  Veroeffentlichen ohne Anstehendes.
- Diagramme: Vektoren tests/chart_vectors.json (dbcalc.chart) und tests/chart_ticks.json (Achsen), Widget == Ansicht
  (gleiche Reihen), Status-Gruppen, Datums-Eimer, kein Zugriff, Veroeffentlichen als SVG ohne Inline-Styles.
- Markdown-Export der neuen Arten.
Wegwerf-Konten w2-a/w2-b/w2-c, raeumt auf. Umgebung wie tests/seiten_widgets.py."""
import base64, hashlib, http.client, json, os, re, secrets, sys, threading, time, urllib.parse
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itsdangerous import TimestampSigner
import auth
from db import connect

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from saga import chartsvg, dbcalc, netguard, weather  # noqa: E402

B = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
HERE = os.path.dirname(os.path.abspath(__file__))
WXP, CDP = (int(x) for x in os.environ.get("WX2_PORTS", "18291,18292").split(","))
res = []
n500 = [0]
TODAY = date.today()


def ok(n, c, info=None):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, "" if c or info is None else f"-> {str(info)[:400]}", flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, cookie=None, body=None):
    c = http.client.HTTPConnection(B.hostname, B.port, timeout=40)
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


def W(kind, cfg=None, bid=None, **kw):
    b = {"type": "widget", "data": dict({"v": 1, "kind": kind, "cfg": cfg or {}}, **kw)}
    if bid:
        b["id"] = bid
    return b


def save(A, nid, blocks):
    s, d = req("PATCH", f"/api/notes/{nid}", A, {"body": json.dumps({"blocks": blocks}), "format": "blocks"})
    assert s == 200, (s, d)
    return json.loads(req("GET", f"/api/notes/{nid}", A)[1]["body"])["blocks"]


def setting(k, v):
    auth.save_setting(k, v)
    time.sleep(5.3)                    # Einstellungen sind im Server 5 s zwischengespeichert


def cleanup():
    with connect() as c, c:
        for t in ("note", "note_version", "user_kv", "file", "note_publish", "public_name", "task", "cal_account"):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE 'w2-%'")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'w2-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'w2-%'")


# =====================================================================================================
# Fake-Open-Meteo
# =====================================================================================================
class WX:
    mode = "ok"
    hits = 0
    geo_hits = 0
    last = {}
    slow = 0.0


class WXH(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path == "/_ctl":
            WX.mode = q.get("mode", WX.mode)
            return self._send(200, b"{}")
        if u.path == "/v1/search":
            WX.geo_hits += 1
            body = {"results": [{"name": "Hamburg", "admin1": "Hamburg", "country": "Deutschland", "latitude": 53.5507, "longitude": 9.993},
                                {"name": "Hamburg<script>", "country": "USA", "latitude": 42.7159, "longitude": -78.8295},
                                {"name": "Kaputt", "latitude": "x", "longitude": 1}]}
            return self._send(200, json.dumps(body).encode())
        if u.path != "/v1/forecast":
            return self._send(404, b"{}")
        WX.hits += 1
        WX.last = q
        if WX.slow:
            time.sleep(WX.slow)
        if WX.mode == "down":
            return self._send(503, b"{}")
        if WX.mode == "redirect":
            self.send_response(302); self.send_header("Location", "http://10.0.0.1/x"); self.end_headers(); return
        if WX.mode == "big":
            return self._send(200, b'{"x":"' + b"a" * 70000 + b'"}')
        t0 = date.today()
        days = [(t0 + timedelta(days=i)).isoformat() for i in range(8)]
        body = {"current": {"temperature_2m": 12.3 if q.get("temperature_unit") != "fahrenheit" else 54.1, "weather_code": 3, "wind_speed_10m": 14},
                "daily": {"time": days, "weather_code": [61, 3, 0, 2, 95, 71, 45, 1], "temperature_2m_max": [13, 14, 15, 16, 17, 18, 19, 20],
                          "temperature_2m_min": [6, 7, 8, 9, 10, 11, 12, 13], "precipitation_probability_max": [70, 10, 0, 5, 90, 40, 20, 0]}}
        return self._send(200, json.dumps(body).encode())

    def _send(self, st, body):
        self.send_response(st); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)


# =====================================================================================================
# Fake-CalDAV (je Benutzer ein Kalender; Termine je Benutzername)
# =====================================================================================================
EVENTS = {}       # benutzer -> [(uid, summary, dtstart, dtend)]


class CDH(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _user(self):
        a = self.headers.get("Authorization", "")
        try:
            u, pw = base64.b64decode(a.split(" ", 1)[1]).decode().split(":", 1)
        except Exception:
            return None
        return u if pw == "app-pw-" + u else None

    def _xml(self, body):
        b = body.encode()
        self.send_response(207); self.send_header("Content-Type", "application/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_PROPFIND(self):
        u = self._user()
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if not u:
            self.send_response(401); self.send_header("Content-Length", "0"); self.end_headers(); return
        if self.headers.get("Depth") == "1":
            return self._xml(f'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:response>'
                             f'<d:href>/cal/{u}/main/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/><c:calendar/></d:resourcetype>'
                             f'<d:displayname>Kalender {u}</d:displayname></d:prop></d:propstat></d:response></d:multistatus>')
        return self._xml(f'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:response><d:href>/</d:href>'
                         f'<d:propstat><d:prop><c:calendar-home-set><d:href>/cal/{u}/</d:href></c:calendar-home-set></d:prop></d:propstat>'
                         f'</d:response></d:multistatus>')

    def do_REPORT(self):
        u = self._user()
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if not u:
            self.send_response(401); self.send_header("Content-Length", "0"); self.end_headers(); return
        parts = []
        for uid, summ, s, e in EVENTS.get(u, []):
            ical = (f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:t\r\nBEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:20261001T000000Z\r\n"
                    f"DTSTART{';VALUE=DATE' if len(s) == 8 else ''}:{s}\r\nDTEND{';VALUE=DATE' if len(e) == 8 else ''}:{e}\r\n"
                    f"SUMMARY:{summ}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
            parts.append(f'<d:response><d:href>/cal/{u}/main/{uid}.ics</d:href><d:propstat><d:prop><d:getetag>"1"</d:getetag>'
                         f'<c:calendar-data>{ical}</c:calendar-data></d:prop></d:propstat></d:response>')
        return self._xml('<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">' + "".join(parts) + "</d:multistatus>")


def serve(port, handler):
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# =====================================================================================================
def unit():
    """Ohne Server: Bereinigung, Vektoren, Achsen, SVG, SSRF-Pruefungen des Moduls."""
    from saga import i18n
    i18n.set_lang("de")                  # Vektoren tragen die deutschen Texte
    from saga import page_widgets as pw
    d = pw.clean_data({"kind": "weather", "cfg": {"place": "<b>Hamburg</b>" + "x" * 100, "lat": 53.55123, "lon": 9.99387, "units": "x", "days": 9, "evil": 1}})
    ok("Wetter: Koordinaten 2 Stellen, Ort Klartext <= 80, Unbekanntes weg", d["cfg"] == {"place": ("Hamburg" + "x" * 100)[:80], "lat": 53.55, "lon": 9.99, "units": "metric", "days": 5}, d)
    d = pw.clean_data({"kind": "weather", "cfg": {"lat": 91, "lon": 0}})
    ok("Wetter: Breite > 90 -> keine Koordinaten", d["cfg"]["lat"] is None and d["cfg"]["lon"] is None)
    d = pw.clean_data({"kind": "habit", "cfg": {"db": 5, "view": "v", "date": "d", "habits": ["a", "a", "b", "<x>", "c", "d", "e", "f", "g"], "range": 9, "goal": 9}})
    ok("Gewohnheiten: max 6, doppelt/ungueltig weg, range 7, goal weg", d["cfg"] == {"db": 5, "view": "v", "date": "d", "habits": ["a", "b", "c", "d", "e", "f"], "range": 7}, d)
    d = pw.clean_data({"kind": "upcoming", "cfg": {"days": 30, "cal": False, "dbs": [{"db": 1, "date": "x"}] * 7 + [{"db": -1, "date": "x"}], "limit": 99}})
    ok("Anstehendes: days 7, cal aus, dbs <= 5, negative ID weg, limit 10", d["cfg"]["days"] == 7 and d["cfg"]["cal"] is False and len(d["cfg"]["dbs"]) == 5 and d["cfg"]["limit"] == 10, d)
    d = pw.clean_data({"kind": "chart", "cfg": {"db": 3, "type": "pie", "x": "s", "series": "t", "stack": True, "cumulative": True, "limit": 99, "agg": "count", "y": "n", "goal": "x"}})
    ok("Diagramm: Reihen nur bei Balken/Linie, limit <= 50, count ohne y", d["cfg"] == {"type": "pie", "x": "s", "agg": "count", "y": "", "sort": "x", "fmt": "num", "limit": 50, "db": 3, "view": ""}, d)
    ok("unbekannte Art bleibt unveraendert", pw.clean_data({"kind": "zukunft", "x": 1}) == {"kind": "zukunft", "x": 1})
    v = json.load(open(os.path.join(HERE, "chart_vectors.json")))
    bad = []
    from zoneinfo import ZoneInfo
    for cs in v["cases"]:
        try:
            got = dbcalc.chart(v["schema"], v["rows"], cs["spec"], tz=ZoneInfo(v["tz"]), today=date.fromisoformat(v["today"]),
                               labels=lambda ids: {i: v["labels"][str(i)] for i in ids if str(i) in v["labels"]})
            got = {k: got[k] for k in cs["want"]}
        except dbcalc.Fail as f:
            got = {"fail": f.code}
        if got != cs["want"]:
            bad.append((cs["name"], got))
    ok(f"Diagramm-Vektoren ({len(v['cases'])} Faelle)", not bad, bad[:2])
    tk = json.load(open(os.path.join(HERE, "chart_ticks.json")))
    bad = [(c, chartsvg.nice_ticks(c["lo"], c["hi"])) for c in tk["cases"] if chartsvg.nice_ticks(c["lo"], c["hi"]) != c["want"]]
    ok(f"Achsen-Vektoren ({len(tk['cases'])} Faelle)", not bad, bad[:2])
    svg = chartsvg.render({"type": "bar", "cats": [{"key": "a", "label": "<A>", "color": "red"}], "series": [{"key": "", "label": "", "values": [3]}], "fmt": "num"})
    ok("SVG: escaped, ohne style-Attribute", "&lt;A&gt;" in svg and " style=" not in svg and "<svg" in svg)
    ok("check_url: http ohne Freigabe abgelehnt", weather.check_url("http://wetter.example.org/v1/forecast")[1] is not None)
    ok("check_url: Zugangsdaten/Query abgelehnt", weather.check_url("https://u:p@x.example.org/f")[1] and weather.check_url("https://x.example.org/f?a=1")[1])
    ok("check_url: fremder Port ohne Freigabe abgelehnt", weather.check_url("https://x.example.org:8443/f")[1] is not None)
    ok("check_url: https oeffentlich ok", weather.check_url("https://x.example.org/v1/forecast") == ("https://x.example.org/v1/forecast", None))
    old = netguard.RESTORE_TEST
    netguard.RESTORE_TEST = True
    try:
        weather._get(f"http://127.0.0.1:{WXP}/v1/forecast", {})
        ok("RESTORE_TEST: kein Abruf", False)
    except weather.WeatherError as ex:
        ok("RESTORE_TEST: kein Abruf", ex.code == "unavailable" and WX.hits == 0)
    finally:
        netguard.RESTORE_TEST = old


def main():
    cleanup()
    serve(WXP, WXH)
    serve(CDP, CDH)
    unit()
    for u, n in (("w2-a", "Anna Zwei"), ("w2-b", "Bert Zwei"), ("w2-c", "Cora Zwei")):
        auth.create_user(u, "Widget2-Pass-123", n)
    A, Bk, C = ck("w2-a"), ck("w2-b"), ck("w2-c")
    s, d = req("GET", "/api/version")
    ok("features weather1/habit1/upcoming1/chart1", all(f in d.get("features", []) for f in ("weather1", "habit1", "upcoming1", "chart1")), d)
    prev_hosts = auth.settings(fresh=True).get("allowed_hosts") or ""
    try:
        part_admin()
        time.sleep(5.3)
        part_weather(A, Bk)
        part_habit(A, Bk, C)
        part_upcoming(A, Bk)
        part_chart(A, Bk, C)
        part_people(A, Bk)
        part_publish(A)
    finally:
        auth.save_setting("allowed_hosts", prev_hosts)
        with connect() as c, c:
            c.execute("DELETE FROM setting WHERE k='weather'")
        auth._settings_cache["v"] = None


def part_admin():
    """Admin-Bereich: Wetter-Schalter (Standard aus), Hinweis Open-Meteo/nicht-kommerziell, Speichern, ungueltige Adresse,
    API-Schluessel verschluesselt und beim Leerlassen behalten."""
    ad = urllib.parse.urlparse(os.environ.get("HK_TEST_ADMIN", "http://127.0.0.1:8098"))
    sa = TimestampSigner(hashlib.sha256(("hk-admin|" + os.environ["SESSION_SECRET"]).encode()).hexdigest())
    auth.create_user("w2-adm", "Widget2-Pass-123", "Admin W2", is_admin=True)
    tok = "w2-csrf-" + secrets.token_hex(6)
    cookie = [sa.sign(base64.b64encode(json.dumps({"aid": "w2-adm", "ep": time.time() + 5, "csrf": tok}).encode())).decode()]

    def go(path, form=None):
        c = http.client.HTTPConnection(ad.hostname, ad.port, timeout=40)
        c.request("POST" if form is not None else "GET", path, body=urllib.parse.urlencode(dict(form, csrf=tok)) if form is not None else None,
                  headers={"Cookie": "hk_admin=" + cookie[0], "Content-Type": "application/x-www-form-urlencoded", "Accept-Language": "de"})
        r = c.getresponse(); data = r.read().decode("utf-8", "replace")
        for k, v in r.getheaders():
            if k.lower() == "set-cookie" and v.startswith("hk_admin="):
                cookie[0] = v.split(";", 1)[0][len("hk_admin="):]
        if r.status >= 500:
            n500[0] += 1
        return r.status, data
    st, html = go("/settings")
    ok(f"Admin: Wetter-Schalter aus, Hinweis nicht-kommerziell ({st})", st == 200 and 'name="weather_enabled"' in html
       and not re.search(r'name="weather_enabled" value="1" checked', html) and "nicht-kommerzielle" in html and "Open-Meteo" in html)
    keep = {}                                # Formular wie der Browser: Textfelder mit Wert, nur angehakte Kaestchen
    for tag in re.findall(r"<input[^>]*>", html):
        nm, val = re.search(r'name="([a-z_]+)"', tag), re.search(r'value="([^"]*)"', tag)
        if not nm or nm.group(1) == "csrf":
            continue
        if 'type="checkbox"' in tag:
            if " checked" in tag:
                keep[nm.group(1)] = "1"
        elif 'type="password"' not in tag:
            keep[nm.group(1)] = val.group(1) if val else ""
    with connect() as c:
        before = dict(c.execute("SELECT k, v FROM setting").fetchall())
    form = dict(keep, weather_enabled="1", weather_base_url="http://wetter.example.org/v1/forecast", weather_geo_url="", weather_api_key="")
    go("/settings", form)
    ok("Admin: http ohne Freigabe abgelehnt (nicht gespeichert)", (auth.settings(fresh=True).get("weather") or {}).get("enabled") is not True)
    form.update(weather_base_url="", weather_api_key="geheimer-schluessel-123")
    go("/settings", form)
    w = auth.settings(fresh=True).get("weather") or {}
    with connect() as c:
        raw = c.execute("SELECT v FROM setting WHERE k='weather'").fetchone()[0]
    ok("Admin: eingeschaltet, Schluessel verschluesselt gespeichert", w.get("enabled") is True and w.get("api_key") == "geheimer-schluessel-123"
       and "geheimer-schluessel" not in raw)
    st, html = go("/settings")
    ok("Admin: Schluessel nicht im Formular, 'gespeichert'", "geheimer-schluessel" not in html and "gespeichert ✓" in html)
    form.update(weather_api_key="")
    go("/settings", form)
    ok("Admin: leer lassen behaelt den Schluessel", (auth.settings(fresh=True).get("weather") or {}).get("api_key") == "geheimer-schluessel-123")
    with connect() as c:
        after = dict(c.execute("SELECT k, v FROM setting").fetchall())
    changed = sorted(k for k in after if k not in ("weather", "mfa", "avatar") and before.get(k, after[k]) != after[k])
    ok(f"Admin: Speichern aendert sonst keine Einstellung ({changed})", not changed)
    with connect() as c, c:                  # Schluessel ganz entfernen (save_setting behielte ihn); vorherige Werte zurueck
        c.execute("DELETE FROM setting WHERE k='weather'")
        c.execute("DELETE FROM setting WHERE NOT (k = ANY(?))", (list(before),))
        for k, v in before.items():
            c.execute("UPDATE setting SET v=? WHERE k=?", (v, k))
        c.execute("DELETE FROM users WHERE username='w2-adm'")
    auth._settings_cache["v"] = None


def part_weather(A, Bk):
    page = req("POST", "/api/notes", A, {"title": "Wetterseite"})[1]["id"]
    save(A, page, [W("weather", {"place": "Hamburg", "lat": 53.55123, "lon": 9.99, "days": 5}, bid="wx", title="Draussen"),
                   W("weather", {"place": "Ohne"}, bid="wx0")])
    s, d = req("GET", f"/api/notes/{page}/widgets", A)
    ok(f"Wetter aus (Standard) -> disabled ({s})", s == 200 and d["widgets"]["wx"]["error"] == "disabled" and d["widgets"]["wx"]["data"] is None, d)
    s, d = req("GET", "/api/widgets/geocode?q=Hamburg", A)
    ok(f"Ortssuche bei aus -> 409 disabled ({s})", s == 409 and d.get("error") == "disabled")
    base = f"http://127.0.0.1:{WXP}"
    auth.save_setting("allowed_hosts", f"127.0.0.1:{WXP}, 127.0.0.1:{CDP}")
    setting("weather", {"enabled": True, "base_url": base + "/v1/forecast", "geo_url": base + "/v1/search", "api_key": ""})
    WX.hits = 0
    s, d = req("GET", f"/api/notes/{page}/widgets?tz=Europe/Berlin", A)
    w = d["widgets"]["wx"]
    ok(f"Wetter: Werte vom Fake ({s})", s == 200 and w["error"] is None and w["data"]["now"] == {"t": 12.3, "code": 3, "wind": 14}
       and len(w["data"]["days"]) == 6 and w["data"]["days"][0]["rain"] == 70 and w["value"] == 12.3 and w["data"]["place"] == "Hamburg", w)
    ok("Wetter: ohne Koordinaten -> bad_cfg", d["widgets"]["wx0"]["error"] == "bad_cfg")
    ok("Server fragt gerundete Koordinaten, timezone=auto", WX.last.get("latitude") == "53.55" and WX.last.get("longitude") == "9.99"
       and WX.last.get("timezone") == "auto", WX.last)
    for _ in range(3):
        req("GET", f"/api/notes/{page}/widgets", A)
    req("GET", f"/api/notes/{page}/widgets", Bk)       # Bert sieht die Seite nicht - darf auch keinen Abruf ausloesen
    ok(f"Zwischenspeicher: ein Abruf fuer 4 Anfragen ({WX.hits})", WX.hits == 1)
    # gleichzeitige Anfragen auf einen neuen Ort: ein Abruf
    save(A, page, [W("weather", {"place": "Bremen", "lat": 53.08, "lon": 8.8, "units": "imperial"}, bid="wx")])
    WX.hits, WX.slow = 0, 0.8
    out = []
    ths = [threading.Thread(target=lambda: out.append(req("GET", f"/api/notes/{page}/widgets", A))) for _ in range(4)]
    [x.start() for x in ths]; [x.join() for x in ths]
    WX.slow = 0
    ok(f"gleichzeitig: ein Abruf ({WX.hits}), alle mit Werten, imperial", WX.hits == 1 and all(o[1]["widgets"]["wx"]["data"]["now"]["t"] == 54.1 for o in out)
       and WX.last.get("temperature_unit") == "fahrenheit")
    # Stale: Server mit kurzer Frische (SAGA_WEATHER_FRESH_S=3)
    WX.mode = "down"
    time.sleep(3.5)
    s, d = req("GET", f"/api/notes/{page}/widgets", A)
    w = d["widgets"]["wx"]
    if WX.hits == 1:
        print("SKIP Stale-Pruefung (Server ohne SAGA_WEATHER_FRESH_S=3)")
    else:
        ok(f"Ausfall: alter Stand mit stale=true ({w.get('error')})", w["error"] is None and w["data"]["stale"] is True, w)
    save(A, page, [W("weather", {"place": "Neu", "lat": 10, "lon": 10}, bid="wx")])
    s, d = req("GET", f"/api/notes/{page}/widgets", A)
    ok("Ausfall ohne Stand -> unavailable", d["widgets"]["wx"]["error"] == "unavailable", d["widgets"]["wx"])
    WX.mode = "redirect"
    save(A, page, [W("weather", {"place": "Umleitung", "lat": 11, "lon": 11}, bid="wx")])
    ok("Weiterleitung wird nicht verfolgt -> unavailable", req("GET", f"/api/notes/{page}/widgets", A)[1]["widgets"]["wx"]["error"] == "unavailable")
    WX.mode = "big"
    save(A, page, [W("weather", {"place": "Gross", "lat": 12, "lon": 12}, bid="wx")])
    ok("Antwort > 64 KB -> unavailable", req("GET", f"/api/notes/{page}/widgets", A)[1]["widgets"]["wx"]["error"] == "unavailable")
    WX.mode = "ok"
    # SSRF: private Adresse ohne Freigabe / http ohne Freigabe
    WX.hits = 0
    setting("weather", {"enabled": True, "base_url": "https://10.255.255.1/v1/forecast", "geo_url": "", "api_key": ""})
    save(A, page, [W("weather", {"place": "Intern", "lat": 13, "lon": 13}, bid="wx")])
    t0 = time.time()
    ok("private Adresse ohne Freigabe -> unavailable, schnell", req("GET", f"/api/notes/{page}/widgets", A)[1]["widgets"]["wx"]["error"] == "unavailable"
       and time.time() - t0 < 4)
    setting("weather", {"enabled": True, "base_url": f"http://localhost:{WXP}/v1/forecast", "geo_url": "", "api_key": ""})
    ok("http ohne Freigabe (anderer Hostname) -> unavailable, kein Abruf", req("GET", f"/api/notes/{page}/widgets", A)[1]["widgets"]["wx"]["error"] == "unavailable"
       and WX.hits == 0)
    setting("weather", {"enabled": True, "base_url": base + "/v1/forecast", "geo_url": base + "/v1/search", "api_key": ""})
    s, d = req("GET", "/api/widgets/geocode?q=Hamburg", A)
    ok(f"Ortssuche: gueltige Treffer, Text bereinigt, gerundet ({s})", s == 200 and len(d["results"]) == 2 and d["results"][0] == {
        "name": "Hamburg", "admin": "Hamburg", "country": "Deutschland", "lat": 53.55, "lon": 9.99} and "<" not in json.dumps(d), d)
    ok("Ortssuche: zu kurz -> 400", req("GET", "/api/widgets/geocode?q=H", A)[0] == 400)
    ok("Ortssuche: ohne Anmeldung -> 401", req("GET", "/api/widgets/geocode?q=Hamburg")[0] == 401)
    st = [req("GET", "/api/widgets/geocode?q=Berlin", Bk)[0] for _ in range(31)]
    n200 = st.index(429) if 429 in st else len(st)      # (Wiederholung binnen einer Minute: Zaehler laeuft noch)
    ok(f"Ortssuche: hoechstens 30/min je Konto, dann 429 ({n200} x 200)", 429 in st and n200 <= 30 and set(st[n200:]) == {429})
    ok("Ortssuche: anderes Konto nicht betroffen", req("GET", "/api/widgets/geocode?q=Berlin", ck("w2-c"))[0] == 200)
    save(A, page, [W("weather", {"place": "Hamburg", "lat": 53.55, "lon": 9.99, "days": 3}, bid="wx", title="Draussen")])
    s, d = req("GET", f"/api/notes/{page}/widgets", A)
    ok("Wetter: days=3 -> heute + 3", len(d["widgets"]["wx"]["data"]["days"]) == 4)
    s, md = req("GET", f"/api/notes/{page}/export", A)
    ok("Markdown-Export Wetter", isinstance(md, str) and "**Draussen:** Hamburg: 12,3 °C" in md, str(md)[:300])
    globals()["WX_PAGE"] = page


def part_habit(A, Bk, C):
    db = req("POST", "/api/notes", A, {"title": "Tageslog", "kind": "database"})[1]["id"]
    schema = {"props": [{"id": "datum", "name": "Datum", "type": "date"}, {"id": "sport", "name": "Sport", "type": "checkbox"},
                        {"id": "lesen", "name": "Lesen", "type": "checkbox"}, {"id": "txt", "name": "T", "type": "text"}],
              "views": [{"id": "alle", "name": "Alle", "type": "table"}]}
    req("PATCH", f"/api/notes/{db}", A, {"schema": schema})
    d = lambda n: (TODAY - timedelta(days=n)).isoformat()   # noqa: E731
    for n, sp, le in ((0, True, False), (1, True, True), (2, True, False), (4, True, False), (1, False, False), (9, True, True)):
        req("POST", "/api/notes", A, {"title": d(n), "parent_id": db, "props": {"datum": d(n), "sport": sp, "lesen": le}})
    req("POST", "/api/notes", A, {"title": "Zeitraum", "parent_id": db, "props": {"datum": d(3) + "T08:00+00:00", "lesen": True}})
    page = req("POST", "/api/notes", A, {"title": "Gewohnheiten"})[1]["id"]
    save(A, page, [W("habit", {"db": db, "view": "alle", "date": "datum", "habits": ["sport", "lesen"], "range": 7, "goal": 5}, bid="hb", title="Woche"),
                   W("habit", {"db": db, "date": "txt", "habits": ["sport"]}, bid="hbad")])
    s, r = req("GET", f"/api/notes/{page}/widgets?tz=Europe/Berlin", A)
    w = r["widgets"]["hb"]
    hs = {h["pid"]: h for h in (w.get("data") or {}).get("habits", [])}
    ok(f"Raster 7 Tage bis heute ({s})", w["error"] is None and w["data"]["days"][-1] == TODAY.isoformat() and len(w["data"]["days"]) == 7, w)
    ok("Sport: Raster, Serie 3, Quote 57 %", hs["sport"]["done"] == [False, False, True, False, True, True, True] and hs["sport"]["streak"] == 3
       and hs["sport"]["pct"] == 57, hs.get("sport"))
    ok("Lesen: Datum mit Uhrzeit zaehlt am Tag, mehrere Zeilen am Tag = ODER, Serie zaehlt ab gestern (heute noch offen)",
       hs["lesen"]["done"] == [False, False, False, True, False, True, False] and hs["lesen"]["streak"] == 1 and hs["lesen"]["pct"] == 29, hs.get("lesen"))
    ok("Eigentuemer darf abhaken, value = heute erledigt", w["data"]["can_check"] is True and w["value"] == 1 and w["max"] == 2 and w["data"]["goal"] == 5)
    ok("Datum-Eigenschaft kein Datum -> bad_cfg", r["widgets"]["hbad"]["error"] == "bad_cfg")
    s, r = req("POST", f"/api/notes/{page}/widgets/hb/habit?tz=Europe/Berlin", A, {"pid": "lesen", "day": d(2), "on": True})
    lh = {h["pid"]: h for h in r["widget"]["data"]["habits"]}["lesen"] if s == 200 else {}
    ok(f"Abhaken: bestehende Zeile des Tages gesetzt ({s})", s == 200 and lh.get("done", [None] * 7)[4] is True, r)
    n0 = len(req("GET", f"/api/notes/{db}/rows", A)[1]["rows"])
    s, r = req("POST", f"/api/notes/{page}/widgets/hb/habit", A, {"pid": "lesen", "day": d(5), "on": True})
    rows = req("GET", f"/api/notes/{db}/rows", A)[1]["rows"]
    nr = [x for x in rows if x["title"] == d(5)]
    ok(f"Abhaken ohne Zeile: neue Zeile, Titel = Datum ({s})", s == 200 and len(rows) == n0 + 1 and nr and nr[0]["props"].get("lesen") is True
       and nr[0]["props"].get("datum") == d(5), nr)
    s, r = req("POST", f"/api/notes/{page}/widgets/hb/habit", A, {"pid": "sport", "day": d(1), "on": False})
    rows = req("GET", f"/api/notes/{db}/rows", A)[1]["rows"]
    ok(f"Aus: in allen Zeilen des Tages ({s})", s == 200 and not any(x["props"].get("sport") for x in rows if x["props"].get("datum") == d(1)))
    ok("Tag in der Zukunft -> 400", req("POST", f"/api/notes/{page}/widgets/hb/habit", A, {"pid": "sport", "day": d(-1), "on": True})[0] == 400)
    ok("Tag vor dem Zeitraum -> 400", req("POST", f"/api/notes/{page}/widgets/hb/habit", A, {"pid": "sport", "day": d(7), "on": True})[0] == 400)
    ok("nicht gewaehlte Gewohnheit -> 400", req("POST", f"/api/notes/{page}/widgets/hb/habit", A, {"pid": "txt", "day": d(0), "on": True})[0] == 400)
    ok("falscher Typ on -> 400", req("POST", f"/api/notes/{page}/widgets/hb/habit", A, {"pid": "sport", "day": d(0), "on": "ja"})[0] == 400)
    ok("unbekannter Block -> 404", req("POST", f"/api/notes/{page}/widgets/zz/habit", A, {"pid": "sport", "day": d(0), "on": True})[0] == 404)
    # Bert: Seite lesen, Datenbank nur lesen -> kein Abhaken; Cora: gar nichts
    req("PUT", f"/api/notes/{page}/shares", A, {"type": "user", "target": "w2-b", "role": "view"})
    req("PUT", f"/api/notes/{db}/shares", A, {"type": "user", "target": "w2-b", "role": "view"})
    s, r = req("GET", f"/api/notes/{page}/widgets", Bk)
    ok("Bert (DB lesen): Raster sichtbar, can_check false", r["widgets"]["hb"]["error"] is None and r["widgets"]["hb"]["data"]["can_check"] is False)
    ok("Bert (DB lesen): Abhaken -> 403", req("POST", f"/api/notes/{page}/widgets/hb/habit", Bk, {"pid": "sport", "day": d(0), "on": False})[0] == 403)
    req("PUT", f"/api/notes/{db}/shares", A, {"type": "user", "target": "w2-b", "role": "edit"})
    s, r = req("POST", f"/api/notes/{page}/widgets/hb/habit", Bk, {"pid": "sport", "day": d(0), "on": False})
    ok(f"Bert (DB bearbeiten): Abhaken geht ({s})", s == 200 and r["widget"]["data"]["can_check"] is True
       and {h["pid"]: h for h in r["widget"]["data"]["habits"]}["sport"]["done"][-1] is False)
    ok("Cora ohne Seite -> 404", req("POST", f"/api/notes/{page}/widgets/hb/habit", C, {"pid": "sport", "day": d(0), "on": True})[0] == 404)
    req("PUT", f"/api/notes/{page}/shares", A, {"type": "user", "target": "w2-c", "role": "edit"})
    s, r = req("GET", f"/api/notes/{page}/widgets", C)
    ok("Cora (Seite, nicht DB): no_access ohne Namen", r["widgets"]["hb"]["error"] == "no_access" and "Tageslog" not in json.dumps(r))
    ok("Cora: Abhaken -> 403", req("POST", f"/api/notes/{page}/widgets/hb/habit", C, {"pid": "sport", "day": d(0), "on": True})[0] == 403)
    s, md = req("GET", f"/api/notes/{page}/export", A)
    ok("Markdown-Export Gewohnheiten", isinstance(md, str) and "**Woche:** Sport:" in md and "% (" in md, str(md)[:400])
    globals()["HB"] = (db, page)


def part_upcoming(A, Bk):
    tz = "Europe/Berlin"
    d = lambda n: (TODAY + timedelta(days=n))   # noqa: E731
    EVENTS["anna"] = [("a1", "GeheimTermin Anna", d(1).strftime("%Y%m%d") + "T090000Z", d(1).strftime("%Y%m%d") + "T100000Z"),
                      ("a2", "Urlaub Anna", d(2).strftime("%Y%m%d"), d(4).strftime("%Y%m%d"))]
    EVENTS["bert"] = [("b1", "Zahnarzt Bert", d(0).strftime("%Y%m%d") + "T140000Z", d(0).strftime("%Y%m%d") + "T150000Z")]
    cal = f"http://127.0.0.1:{CDP}/"
    for who, ckie in (("anna", A), ("bert", Bk)):
        s, r = req("POST", "/api/settings/calendars/test", ckie, {"base_url": cal, "username": who, "password": "app-pw-" + who})
        cals = r.get("calendars", []) if isinstance(r, dict) else []
        s2, _ = req("POST", "/api/settings/calendars", ckie, {"base_url": cal, "username": who, "password": "app-pw-" + who,
                                                               "calendars": [dict(x, show=True) for x in cals]})
        ok(f"Fake-CalDAV fuer {who} verbunden ({s}, {s2})", s == 200 and s2 == 200 and len(cals) == 1, r)
    req("POST", "/api/tasks", A, {"text": "Steuer Anna", "due": d(3).isoformat()})
    req("POST", "/api/tasks", A, {"text": "Alt Anna", "due": d(-2).isoformat()})
    req("POST", "/api/tasks", A, {"text": "Fern Anna", "due": d(30).isoformat()})
    req("POST", "/api/tasks", Bk, {"text": "Einkauf Bert", "due": d(1).isoformat()})
    db = req("POST", "/api/notes", A, {"title": "Projekte", "kind": "database"})[1]["id"]
    req("PATCH", f"/api/notes/{db}", A, {"schema": {"props": [{"id": "frist", "name": "Frist", "type": "date"}, {"id": "ok", "name": "OK", "type": "checkbox"}],
                                                     "views": [{"id": "alle", "name": "Alle", "type": "table"}]}})
    req("POST", "/api/notes", A, {"title": "Abgabe Projekt", "parent_id": db, "props": {"frist": d(2).isoformat()}})
    req("POST", "/api/notes", A, {"title": "Verpasst offen", "parent_id": db, "props": {"frist": d(-1).isoformat()}})
    req("POST", "/api/notes", A, {"title": "Verpasst erledigt", "parent_id": db, "props": {"frist": d(-1).isoformat(), "ok": True}})
    page = req("POST", "/api/notes", A, {"title": "Agenda"})[1]["id"]
    save(A, page, [W("upcoming", {"days": 7, "dbs": [{"db": db, "view": "alle", "date": "frist"}], "limit": 10}, bid="up", title="Bald"),
                   W("upcoming", {"days": 7, "overdue": False, "cal": False, "limit": 5}, bid="up2")])
    s, r = req("GET", f"/api/notes/{page}/widgets?tz={tz}", A)
    w = r["widgets"]["up"]
    titles = [x["title"] for x in (w.get("data") or {}).get("items", [])]
    ok(f"Anna: eigene Termine, Aufgaben, DB-Zeilen ({s})", s == 200 and w["error"] is None and
       {"GeheimTermin Anna", "Urlaub Anna", "Steuer Anna", "Alt Anna", "Abgabe Projekt", "Verpasst offen"} == set(titles), titles)
    ok("ausserhalb des Zeitraums / erledigt fehlt", "Fern Anna" not in titles and "Verpasst erledigt" not in titles)
    ok("sortiert nach Start, ueberfaellig markiert", titles[0] in ("Alt Anna", "Verpasst offen") and
       all(x["overdue"] for x in w["data"]["items"] if x["title"] in ("Alt Anna", "Verpasst offen")), w["data"]["items"][:3])
    it = {x["title"]: x for x in w["data"]["items"]}
    ok("Formate: event/task/row mit note/task-ID, ganztaegig", it["GeheimTermin Anna"]["k"] == "event" and it["GeheimTermin Anna"]["allday"] is False
       and it["Urlaub Anna"]["allday"] is True and it["Steuer Anna"]["k"] == "task" and isinstance(it["Steuer Anna"]["task"], int)
       and it["Abgabe Projekt"]["k"] == "row" and isinstance(it["Abgabe Projekt"]["note"], int), it)
    w2 = r["widgets"]["up2"]
    ok("ohne Kalender, ohne ueberfaellig, Limit 5", [x["k"] for x in w2["data"]["items"]] == ["task"] and w2["data"]["items"][0]["title"] == "Steuer Anna", w2)
    # Leck-Test: Seite mit Bert geteilt - Bert sieht NUR seine eigenen Daten (mehrfach abgerufen, abwechselnd)
    req("PUT", f"/api/notes/{page}/shares", A, {"type": "user", "target": "w2-b", "role": "view"})
    leak = False
    for i in range(3):
        for who, ckie in (("anna", A), ("bert", Bk)):
            s, r = req("GET", f"/api/notes/{page}/widgets?tz={tz}", ckie)
            raw = json.dumps(r, ensure_ascii=False)
            if who == "bert" and ("Anna" in raw or "Abgabe" in raw or "Verpasst" in raw):
                leak = True
            if who == "anna" and "Bert" in raw:
                leak = True
    s, r = req("GET", f"/api/notes/{page}/widgets?tz={tz}", Bk)
    bt = [x["title"] for x in r["widgets"]["up"]["data"]["items"]]
    ok("Leck-Test: kein Termin/keine Aufgabe des anderen Kontos (3 Runden abwechselnd)", not leak, r)
    ok(f"Bert sieht eigene Termine und Aufgaben, DB von Anna ohne Recht zaehlt als Fehler: {bt}",
       set(bt) == {"Zahnarzt Bert", "Einkauf Bert"} and r["widgets"]["up"]["data"]["errors"] == 1)
    s, r = req("POST", f"/api/notes/{page}/widgets/preview", A, {"block": W("upcoming", {"days": 1, "cal": True, "tasks": False}, bid="pv")})
    ok("Vorschau: nur heute (Bert-Termin nicht dabei)", s == 200 and r["widget"]["data"]["items"] == [], r)
    globals()["UP_PAGE"] = page


def part_chart(A, Bk, C):
    db = req("POST", "/api/notes", A, {"title": "Ausgaben", "kind": "database"})[1]["id"]
    st = {"id": "status", "name": "Status", "type": "select", "status": True, "options": [
        {"id": "neu", "name": "Neu", "color": "gray", "group": "todo"}, {"id": "lauf", "name": "Läuft", "color": "blue", "group": "doing"},
        {"id": "fertig", "name": "Fertig", "color": "green", "group": "done"}, {"id": "archiv", "name": "Archiv", "color": "brown", "group": "done"}]}
    schema = {"props": [st, {"id": "betrag", "name": "Betrag", "type": "number"}, {"id": "tag", "name": "Tag", "type": "date"},
                        {"id": "kat", "name": "Kategorie", "type": "multi_select", "options": [{"id": "a", "name": "Essen", "color": "red"}, {"id": "b", "name": "Reise"}]}],
              "views": [{"id": "alle", "name": "Alle", "type": "table"},
                        {"id": "dia", "name": "Diagramm", "type": "chart", "filters": [{"pid": "betrag", "op": "gt", "value": "0"}],
                         "chart": {"type": "bar", "x": "status", "status": True, "agg": "sum", "y": "betrag", "fmt": "eur", "bogus": 1}},
                        {"id": "zeit", "name": "Zeit", "type": "chart", "chart": {"type": "line", "x": "tag", "bucket": "month", "agg": "count", "cumulative": True}}]}
    s, _ = req("PATCH", f"/api/notes/{db}", A, {"schema": schema})
    sc = req("GET", f"/api/notes/{db}/rows?limit=1", A)[1]["schema"]
    v = {x["id"]: x for x in sc["views"]}
    ok(f"Ansicht chart gespeichert, Spec bereinigt ({s})", s == 200 and v["dia"]["type"] == "chart" and "bogus" not in v["dia"]["chart"]
       and v["dia"]["chart"]["status"] is True and v["dia"]["chart"]["fmt"] == "eur", v.get("dia"))
    for ti, stv, b, tg, k in (("e1", "neu", 10, "2026-01-15", ["a"]), ("e2", "lauf", 20.5, "2026-01-20", ["a", "b"]), ("e3", "fertig", 30, "2026-03-02", ["b"]),
                              ("e4", "archiv", 5, "2026-03-31", []), ("e5", "neu", -3, "2026-02-10", ["a"])):
        req("POST", "/api/notes", A, {"title": ti, "parent_id": db, "props": {"status": stv, "betrag": b, "tag": tg, "kat": k}})
    s, r = req("GET", f"/api/notes/{db}/chart?view=dia&tz=Europe/Berlin", A)
    c = r.get("chart") or {}
    ok(f"Ansicht: Status-Gruppen, Filter der Ansicht, Summe ({s})", s == 200 and [x["key"] for x in c.get("cats", [])] == ["todo", "doing", "done"]
       and c["series"][0]["values"] == [10, 20.5, 35] and c["total"] == 65.5 and c["fmt"] == "eur", r)
    s, r = req("GET", f"/api/notes/{db}/chart?view=zeit", A)
    c = r.get("chart") or {}
    ok(f"Ansicht: Monats-Eimer ohne Luecken, kumulativ ({s})", [x["key"] for x in c.get("cats", [])] == ["2026-01", "2026-02", "2026-03"]
       and c["series"][0]["values"] == [2, 3, 5] and c["cats"][0]["start"] == "2026-01-01" and c["x_kind"] == "date", c)
    ok("Ansicht ohne chart -> error bad_cfg", req("GET", f"/api/notes/{db}/chart?view=alle", A)[1].get("error") == "bad_cfg")
    ok("tz ungueltig -> 400", req("GET", f"/api/notes/{db}/chart?tz=Mars/Base", A)[0] == 400)
    ok("Bert ohne Zugriff -> 404", req("GET", f"/api/notes/{db}/chart?view=dia", Bk)[0] == 404)
    page = req("POST", "/api/notes", A, {"title": "Dashboard"})[1]["id"]
    spec = dict(v["dia"]["chart"])
    save(A, page, [W("chart", dict(spec, db=db, view="dia"), bid="ch", title="Nach Status"),
                   W("chart", {"db": db, "type": "pie", "x": "kat"}, bid="pie"),
                   W("chart", {"db": db, "type": "bar", "x": "tag", "bucket": "quarter", "series": "kat", "stack": True}, bid="ser"),
                   W("chart", {"db": db, "type": "number", "agg": "avg", "y": "betrag", "goal": 100}, bid="num"),
                   W("chart", {"db": db, "type": "bar", "x": "gibtsnicht"}, bid="bad")])
    s, r = req("GET", f"/api/notes/{page}/widgets", A)
    ws = r["widgets"]
    view_res = req("GET", f"/api/notes/{db}/chart?view=dia", A)[1]["chart"]
    strip = lambda x: {k: v for k, v in x.items() if k != "as_of"}   # noqa: E731
    ok("Widget == Ansicht (eine Logik)", strip(ws["ch"]["data"]) == strip(view_res) and ws["ch"]["value"] == 65.5 and ws["ch"]["fmt"] == "eur", (ws["ch"], view_res))
    ok("Kreis nach Mehrfachauswahl: Zeile zaehlt je Option", [(x["key"], x["color"]) for x in ws["pie"]["data"]["cats"]] == [("a", "red"), ("b", "gray")]
       and ws["pie"]["data"]["series"][0]["values"] == [3, 2], ws["pie"])
    ok("Reihen + gestapelt (Quartale)", [x["key"] for x in ws["ser"]["data"]["cats"]] == ["2026-Q1"] and len(ws["ser"]["data"]["series"]) == 2
       and ws["ser"]["data"]["stack"] is True, ws["ser"])
    ok("Zahl: total = Durchschnitt, Ziel", ws["num"]["data"]["total"] == 12.5 and ws["num"]["max"] == 100 and ws["num"]["data"]["cats"] == [], ws["num"])
    ok("unbekannte Eigenschaft -> bad_cfg", ws["bad"]["error"] == "bad_cfg" and ws["bad"]["data"] is None)
    req("PUT", f"/api/notes/{page}/shares", A, {"type": "user", "target": "w2-c", "role": "view"})
    s, r = req("GET", f"/api/notes/{page}/widgets", C)
    ok("Cora (Seite ja, DB nein): no_access ohne Werte/Namen", r["widgets"]["ch"]["error"] == "no_access" and r["widgets"]["ch"]["data"] is None
       and "Essen" not in json.dumps(r) and "Ausgaben" not in json.dumps(r))
    req("POST", "/api/notes", A, {"title": "e6", "parent_id": db, "props": {"status": "lauf", "betrag": 1}})
    s, r = req("GET", f"/api/notes/{page}/widgets", A)
    ok("Aenderung an der Quelle rechnet sofort neu (Zwischenspeicher am Quellstand)", r["widgets"]["ch"]["data"]["series"][0]["values"] == [10, 21.5, 35])
    s, md = req("GET", f"/api/notes/{page}/export", A)
    ok("Markdown-Export Diagramm", isinstance(md, str) and "**Nach Status:** Zu erledigen:" in md, str(md)[:400])
    globals()["CH"] = (db, page)


def part_people(A, Bk):
    """Integration Paket 1/5: Diagramm nach Person (Anzeigename, nie Anmeldename), Datei-Spalte abgelehnt, Datum mit
    Uhrzeit in der Zeitzone der Anfrage, Anstehendes "nur mir zugewiesene"."""
    from saga.core import local_ref
    ha, hb = local_ref("w2-a"), local_ref("w2-b")
    db = req("POST", "/api/notes", A, {"title": "Team", "kind": "database"})[1]["id"]
    st = {"id": "status", "name": "Status", "type": "select", "status": True, "options": [
        {"id": "neu", "name": "Neu", "group": "todo"}, {"id": "fertig", "name": "Fertig", "group": "done"}]}
    req("PATCH", f"/api/notes/{db}", A, {"schema": {"props": [{"id": "wer", "name": "Wer", "type": "person"}, {"id": "anh", "name": "Anhang", "type": "files"},
                                                              {"id": "frist", "name": "Frist", "type": "date"}, st],
                                                    "views": [{"id": "alle", "name": "Alle", "type": "table"},
                                                              {"id": "pp", "name": "Je Person", "type": "chart", "chart": {"type": "bar", "x": "wer"}},
                                                              {"id": "pf", "name": "Je Datei", "type": "chart", "chart": {"type": "bar", "x": "anh"}},
                                                              {"id": "ps", "name": "Status je Person", "type": "chart",
                                                               "chart": {"type": "bar", "x": "status", "status": True, "series": "wer"}},
                                                              {"id": "pt", "name": "Tage", "type": "chart", "chart": {"type": "bar", "x": "frist", "bucket": "day"}}]}})
    req("PUT", f"/api/notes/{db}/shares", A, {"type": "user", "target": "w2-b", "role": "edit"})
    d = lambda n: (TODAY + timedelta(days=n)).isoformat()   # noqa: E731
    for ti, wer, fr, stv in (("P1", [ha, hb], d(1) + "T10:00+00:00", "neu"), ("P2", [hb], d(2), "fertig"), ("P3", [], d(0), "neu"),
                             ("P4", [ha], d(0) + "/" + d(3), "neu"), ("P5", [ha], d(4) + "T23:30+00:00", "neu")):
        s, r = req("POST", "/api/notes", A, {"title": ti, "parent_id": db, "props": {"wer": wer, "frist": fr, "status": stv}})
        ok(f"Zeile {ti} mit Personen ({s})", s == 200, r)
    s, r = req("GET", f"/api/notes/{db}/chart?view=pp&ids=1", A)
    c = r.get("chart") or {}
    raw = json.dumps(r, ensure_ascii=False)
    ok(f"Diagramm nach Person: Anzeigenamen, Kennungen als key ({s})", s == 200 and sorted((x["key"], x["label"]) for x in c.get("cats", []))
       == sorted([(ha, "Anna Zwei"), (hb, "Bert Zwei")]) and dict(zip([x["label"] for x in c["cats"]], c["series"][0]["values"])) == {"Anna Zwei": 3, "Bert Zwei": 2}, r)
    ok("Diagramm nach Person: kein Anmeldename in der Antwort", "w2-a" not in raw and "w2-b" not in raw, raw)
    s, r = req("GET", f"/api/notes/{db}/chart?view=pp", Bk)
    ok("Bert (geteilt) sieht dieselben Gruppen", s == 200 and sorted(x["label"] for x in r["chart"]["cats"]) == ["Anna Zwei", "Bert Zwei"]
       and "w2-a" not in json.dumps(r), r)
    ok("Datei-Spalte als Achse -> bad_cfg", req("GET", f"/api/notes/{db}/chart?view=pf", A)[1].get("error") == "bad_cfg")
    s, r = req("GET", f"/api/notes/{db}/chart?view=ps", A)
    c = r.get("chart") or {}
    ok("Status-Gruppen mit Reihen je Person", [x["key"] for x in c.get("cats", [])] == ["todo", "done"]
       and {x["label"]: x["values"] for x in c["series"]} == {"Anna Zwei": [3, 0], "Bert Zwei": [1, 1]}, c)
    s, r = req("GET", f"/api/notes/{db}/chart?view=pt&tz=Pacific/Kiritimati", A)
    keys = [x["key"] for x in (r.get("chart") or {}).get("cats", [])]
    ok("Datum mit Uhrzeit: Tag in der Zeitzone der Anfrage (+14 h -> 23:30 UTC ist der Folgetag)", d(5) in keys and d(4) not in keys[-1:], keys)
    page = req("POST", "/api/notes", A, {"title": "Meins"})[1]["id"]
    save(A, page, [W("upcoming", {"days": 7, "cal": False, "tasks": False, "dbs": [{"db": db, "date": "frist", "mine": True}]}, bid="um"),
                   W("upcoming", {"days": 7, "cal": False, "tasks": False, "dbs": [{"db": db, "date": "frist"}]}, bid="ua"),
                   W("chart", {"db": db, "type": "pie", "x": "wer"}, bid="cw", title="JePerson")])
    s, r = req("GET", f"/api/notes/{page}/widgets?tz=Europe/Berlin", A)
    um = [x["title"] for x in r["widgets"]["um"]["data"]["items"]]
    ua = [x["title"] for x in r["widgets"]["ua"]["data"]["items"]]
    ok(f"Anstehendes nur mir zugewiesene (Anna): {um}", sorted(um) == ["P1", "P4", "P5"] and sorted(ua) == ["P1", "P2", "P3", "P4", "P5"], r["widgets"]["ua"])
    it = {x["title"]: x for x in r["widgets"]["ua"]["data"]["items"]}
    ok("Anstehendes: Zeitraum (Ende) und Uhrzeit", it["P4"]["end"] == d(3) and it["P4"]["allday"] is True and it["P1"]["allday"] is False, it)
    ok("Diagramm-Widget nach Person", sorted(x["label"] for x in r["widgets"]["cw"]["data"]["cats"]) == ["Anna Zwei", "Bert Zwei"]
       and "w2-a" not in json.dumps(r), r["widgets"]["cw"])
    req("PUT", f"/api/notes/{page}/shares", A, {"type": "user", "target": "w2-b", "role": "view"})
    s, r = req("GET", f"/api/notes/{page}/widgets?tz=Europe/Berlin", Bk)
    um = [x["title"] for x in r["widgets"]["um"]["data"]["items"]]
    ok(f"Anstehendes nur mir zugewiesene (Bert): {um}", sorted(um) == ["P1", "P2"], r["widgets"]["um"])
    pub = req("POST", "/api/notes", A, {"title": "PersonenPub"})[1]["id"]
    save(A, pub, [W("chart", {"db": db, "type": "bar", "x": "wer"}, bid="pp", title="PubPerson"),
                  W("chart", {"db": db, "type": "bar", "x": "status", "status": True}, bid="ps", title="PubStatus"),
                  {"type": "database", "data": {"db": db, "view": "pp"}}])
    s, dd = req("PUT", f"/api/notes/{pub}/publish", A, {"slug": "w2-personen", "name": "w2-anna"})
    url = urllib.parse.urlparse(str((dd or {}).get("url") or "")).path if s == 200 else ""
    s, html = req("GET", url) if url else (s, "")
    html = str(html)
    ok(f"veroeffentlicht: Diagramm nach Status ja, nach Person ohne Namen ({s})", s == 200 and "PubStatus" in html and html.count('<svg class="pub-ch"') == 1
       and "Anna Zwei" not in html and "Bert Zwei" not in html and ha not in html and hb not in html, html[:300])
    req("DELETE", f"/api/notes/{pub}/publish", A)


def part_publish(A):
    db, _ = CH
    hdb, _ = HB
    pub = req("POST", "/api/notes", A, {"title": "Oeffentlich2"})[1]["id"]
    save(A, pub, [W("weather", {"place": "Hamburg", "lat": 53.55, "lon": 9.99, "days": 3}, bid="pw", title="WetterPub"),
                  W("weather", {"place": "NieAbgerufen", "lat": 1.23, "lon": 4.56}, bid="pw2", title="WetterLeer"),
                  W("upcoming", {"days": 7}, bid="pu", title="AgendaPub"),
                  W("habit", {"db": hdb, "date": "datum", "habits": ["sport"]}, bid="ph", title="HabitPub"),
                  W("chart", {"db": db, "type": "donut", "x": "status", "status": True}, bid="pc", title="ChartPub"),
                  {"type": "database", "data": {"db": db, "view": "zeit"}}])
    WX.hits = 0
    s, d = req("PUT", f"/api/notes/{pub}/publish", A, {"slug": "widgets2", "name": "w2-anna"})
    url = urllib.parse.urlparse(str((d or {}).get("url") or "")).path if s == 200 else ""
    s, html = req("GET", url) if url else (s, "")
    ok(f"veroeffentlicht ({s})", s == 200 and "pub-wg" in str(html), str(html)[:200])
    html = str(html)
    ok("Wetter: letzter Stand mit Quellenangabe, Besucher loest keinen Abruf aus", "WetterPub" in html and "Open-Meteo.com" in html and WX.hits == 0)
    ok("Wetter ohne Zwischenspeicher entfaellt", "WetterLeer" not in html)
    ok("Anstehendes nie veroeffentlicht", "AgendaPub" not in html and "Steuer" not in html and "GeheimTermin" not in html)
    ok("Gewohnheiten als Raster ohne Klick", "HabitPub" in html and "pub-hb" in html and "<button" not in html.split("<body", 1)[-1])
    ok("Diagramm-Widget und Diagramm-Ansicht als SVG", "ChartPub" in html and html.count('<svg class="pub-ch"') == 2)
    ok("keine Inline-Styles, kein Skript im Inhalt", " style=" not in html.split("<body", 1)[-1] and "<script" not in html.split("<main", 1)[-1])
    req("DELETE", f"/api/notes/{pub}/publish", A)


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
    print(f"\nWIDGETS2 {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)
