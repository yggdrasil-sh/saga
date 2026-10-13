"""API-Test externe Kalender (in der Probe-Instanz gegen Radicale auf 127.0.0.1:5232)."""
import json, os, base64, sqlite3, sys, httpx, urllib.request, urllib.parse
from itsdangerous import TimestampSigner
B = os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"); DB = os.environ.get("HK_TEST_DB", "/tmp/hkstage/saga.db")
S = TimestampSigner(os.environ["SESSION_SECRET"]); ck = lambda u: S.sign(base64.b64encode(json.dumps({"uid": u}).encode())).decode()
A, X = ck("hk-test-a"), ck("hk-test-b"); RAD, PW = "http://127.0.0.1:5232/", "geheim-app-pw-123"
def call(m, p, c, b=None):
    h = {"Host": urllib.parse.urlparse(os.environ["BASE_URL"]).netloc, "Cookie": "saga=" + c, "X-Saga": "1", "Content-Type": "application/json", "Accept-Language": "de"}   # Meldungen werden auf Deutsch geprueft
    try:
        with urllib.request.urlopen(urllib.request.Request(B + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers=h), timeout=30) as r:
            raw = r.read(); return r.status, json.loads(raw), raw.decode()
    except urllib.error.HTTPError as e:
        raw = e.read(); return e.code, (json.loads(raw) if raw[:1] == b"{" else {}), raw.decode(errors="replace")
res = []
def ok(n, c): res.append(bool(c)); print(("OK  " if c else "FAIL"), n)
for cal in ("arbeit", "privat"):
    httpx.request("MKCALENDAR", f"{RAD}test/{cal}/", auth=("test", PW), content=f'<?xml version="1.0"?><c:mkcalendar xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:set><d:prop><d:displayname>{cal.title()}</d:displayname></d:prop></d:set></c:mkcalendar>')
httpx.put(f"{RAD}test/arbeit/t1.ics", auth=("test", PW), headers={"Content-Type": "text/calendar"},
          content="BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:t\r\nBEGIN:VEVENT\r\nUID:t1\r\nDTSTAMP:20261001T000000Z\r\nDTSTART:20261009T120000Z\r\nDTEND:20261009T130000Z\r\nSUMMARY:Frauenarzt\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
httpx.put(f"{RAD}test/privat/t2.ics", auth=("test", PW), headers={"Content-Type": "text/calendar"},
          content="BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:t\r\nBEGIN:VEVENT\r\nUID:t2\r\nDTSTAMP:20261001T000000Z\r\nDTSTART;VALUE=DATE:20261020\r\nDTEND;VALUE=DATE:20261023\r\nSUMMARY:Urlaub\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
c, d, _ = call("GET", "/api/settings/calendars", A); ok("Liste leer + erlaubte Server", c == 200 and d["accounts"] == [] and "127.0.0.1:5232" in d["allowed"])
c, d, _ = call("POST", "/api/settings/calendars/test", A, {"base_url": RAD, "username": "test", "password": "falsch"}); ok(f"Falsches Passwort -> {c}: {d.get('error')}", c == 400 and "App-Passwort" in d.get("error", ""))
c, d, _ = call("POST", "/api/settings/calendars/test", A, {"base_url": "https://169.254.169.254/", "username": "x", "password": "y"}); ok(f"Fremder Server -> {c}", c == 400 and "nicht freigegeben" in d.get("error", ""))
c, d, _ = call("POST", "/api/settings/calendars/test", A, {"base_url": RAD, "username": "test", "password": PW}); cals = d.get("calendars", [])
ok(f"Verbindung testen: {[x['name'] for x in cals]}", c == 200 and len(cals) == 2)
sel = [dict(x, show=True, color="#ff0000") for x in cals] + [{"href": "http://127.0.0.1:5232/test/fremd/", "name": "untergeschoben", "show": True}, {"href": "http://127.0.0.1:8080/api/notes", "show": True}]
c, d, raw = call("POST", "/api/settings/calendars", A, {"base_url": RAD, "username": "test", "password": PW, "label": "Testkonto", "calendars": sel}); aid = d.get("id")
ok(f"Speichern: untergeschobene Kalender verworfen ({[x['name'] for x in d.get('calendars', [])]})", c == 200 and len(d["calendars"]) == 2)
c, d, raw = call("GET", "/api/settings/calendars", A); ok("Passwort in KEINER Antwort", PW not in raw and "secret" not in raw and d["accounts"][0]["label"] == "Testkonto")
row = sqlite3.connect(DB).execute("SELECT secret FROM cal_account WHERE id=?", (aid,)).fetchone()[0]; ok("In der Datenbank nur verschluesselt", PW not in row and row.startswith("gAAAA"))
c, d, _ = call("GET", "/api/calendar/events?start=2026-10-01&end=2026-11-01", A)
ok(f"Termine: {[(e['summary'], e['calendar'], e['color']) for e in d.get('events', [])]}", c == 200 and {e["summary"] for e in d["events"]} == {"Frauenarzt", "Urlaub"} and not d["errors"])
ok("Zeitraum zu lang -> 400", call("GET", "/api/calendar/events?start=2026-01-01&end=2026-06-01", A)[0] == 400)
ok("Zeitraum unsinnig -> 400", call("GET", "/api/calendar/events?start=x&end=2026-06-01", A)[0] == 400)
hide = [dict(x, show=x["name"] != "Privat") for x in call("GET", "/api/settings/calendars", A)[1]["accounts"][0]["calendars"]]
ok("Auswahl aendern ohne Passwort", call("PATCH", f"/api/settings/calendars/{aid}", A, {"calendars": hide})[0] == 200)
ok("Ausgeblendeter Kalender fehlt", {e["summary"] for e in call("GET", "/api/calendar/events?start=2026-10-01&end=2026-11-01", A)[1]["events"]} == {"Frauenarzt"})
ok("Neues falsches Passwort wird abgelehnt (altes bleibt)", call("PATCH", f"/api/settings/calendars/{aid}", A, {"password": "falsch"})[0] == 400
   and call("GET", "/api/calendar/events?start=2026-10-01&end=2026-11-01", A)[1]["events"])
c, d, raw = call("GET", "/api/settings/calendars", X); ok("Anderer Benutzer sieht nichts", d["accounts"] == [])
ok("Anderer Benutzer: testen mit fremdem Zugang -> 404", call("POST", "/api/settings/calendars/test", X, {"id": aid})[0] == 404)
ok("Anderer Benutzer: aendern/loeschen -> 404", call("PATCH", f"/api/settings/calendars/{aid}", X, {"calendars": []})[0] == 404 and call("DELETE", f"/api/settings/calendars/{aid}", X)[0] == 404)
ok("Anderer Benutzer: keine fremden Termine", call("GET", "/api/calendar/events?start=2026-10-01&end=2026-11-01", X)[1]["events"] == [])
ok("Ohne Anmeldung -> 401", call("GET", "/api/settings/calendars", "x")[0] == 401)
for i in range(4): call("POST", "/api/settings/calendars", A, {"base_url": RAD, "username": "test", "password": PW, "calendars": []})
ok("Hoechstens 5 Zugaenge", call("POST", "/api/settings/calendars", A, {"base_url": RAD, "username": "test", "password": PW, "calendars": []})[0] == 400)
ok("Entfernen", call("DELETE", f"/api/settings/calendars/{aid}", A)[0] == 200 and all(a["id"] != aid for a in call("GET", "/api/settings/calendars", A)[1]["accounts"]))
print(f"\nKALENDER-API {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
cn = sqlite3.connect(DB); cn.execute("DELETE FROM cal_account WHERE uid LIKE ?", ("hk-test-%",)); cn.commit()
sys.exit(0 if all(res) else 1)
