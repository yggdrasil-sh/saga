"""API-Test: Termine anlegen/aendern/loeschen (Probe-Instanz + Radicale auf 127.0.0.1:5232)."""
import json, os, base64, sqlite3, sys, httpx, urllib.request, urllib.parse
from itsdangerous import TimestampSigner
B = os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"); DB = os.environ.get("HK_TEST_DB", "/tmp/hkstage/saga.db")
S = TimestampSigner(os.environ["SESSION_SECRET"]); ck = lambda u: S.sign(base64.b64encode(json.dumps({"uid": u}).encode())).decode()
A, X = ck("hk-test-a"), ck("hk-test-b"); RAD, PW = "http://127.0.0.1:5232/", "geheim-app-pw-123"; AUTH = ("test", PW)
def call(m, p, c, b=None):
    h = {"Host": urllib.parse.urlparse(os.environ["BASE_URL"]).netloc, "Cookie": "saga=" + c, "X-Saga": "1", "Content-Type": "application/json"}
    try:
        with urllib.request.urlopen(urllib.request.Request(B + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers=h), timeout=30) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read(); return e.code, (json.loads(raw) if raw[:1] == b"{" else {})
res = []
def ok(n, c): res.append(bool(c)); print(("OK  " if c else "FAIL"), n)
MK = '<?xml version="1.0"?><c:mkcalendar xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:set><d:prop><d:displayname>{}</d:displayname></d:prop></d:set></c:mkcalendar>'
for cal in ("arbeit", "privat"): httpx.request("MKCALENDAR", f"{RAD}test/{cal}/", auth=AUTH, content=MK.format(cal.title()))
cals = call("POST", "/api/settings/calendars/test", A, {"base_url": RAD, "username": "test", "password": PW})[1]["calendars"]
aid = call("POST", "/api/settings/calendars", A, {"base_url": RAD, "username": "test", "password": PW, "calendars": [dict(c, show=True) for c in cals]})[1]["id"]
AR = next(c["href"] for c in cals if c["name"] == "Arbeit"); PR = next(c["href"] for c in cals if c["name"] == "Privat")
evs = lambda: call("GET", "/api/calendar/events?start=2026-10-01&end=2026-11-01&fresh=1", A)[1]["events"]   # am Zwischenspeicher vorbei
c, d = call("POST", "/api/calendar/events", A, {"account": aid, "cal": AR, "summary": "Zahnarzt", "start": "2026-10-14T08:30:00+02:00", "end": "2026-10-14T09:00:00+02:00", "location": "Praxis"})
ok("Termin anlegen", c == 200 and d.get("href", "").startswith(AR))
c, d = call("POST", "/api/calendar/events", A, {"account": aid, "cal": PR, "summary": "Herbstferien", "allday": True, "start": "2026-10-26", "end": "2026-10-31"})
ok("Ganztaegig anlegen", c == 200)
z = next((e for e in evs() if e["summary"] == "Zahnarzt"), None); ok("Angelegter Termin erscheint (Ortszeit)", z and z["start"].startswith("2026-10-14T08:30") and z["location"] == "Praxis")
c, d = call("PATCH", "/api/calendar/events", A, {"account": aid, "cal": AR, "href": z["href"], "etag": z["etag"], "summary": "Zahnarzt (verschoben)", "start": "2026-10-15T10:00:00+02:00", "end": "2026-10-15T10:30:00+02:00"})
ok("Aendern", c == 200 and any(e["summary"] == "Zahnarzt (verschoben)" and e["start"].startswith("2026-10-15T10:00") for e in evs()))
c, d = call("PATCH", "/api/calendar/events", A, {"account": aid, "cal": AR, "href": z["href"], "etag": z["etag"], "summary": "x", "start": "2026-10-15T10:00:00+02:00"})
ok(f"Veraltetes ETag -> Konflikt ({d.get('error', '')[:40]}…)", c == 400 and "inzwischen" in d.get("error", ""))
alarm = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:t\r\nBEGIN:VEVENT\r\nUID:mit-alarm\r\nDTSTAMP:20261001T000000Z\r\nDTSTART:20261020T070000Z\r\nDTEND:20261020T080000Z\r\nSUMMARY:Mit Erinnerung\r\nCATEGORIES:Arzt\r\nBEGIN:VALARM\r\nACTION:DISPLAY\r\nTRIGGER:-PT30M\r\nDESCRIPTION:Erinnerung\r\nEND:VALARM\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
httpx.put(f"{AR}mit-alarm.ics", auth=AUTH, content=alarm, headers={"Content-Type": "text/calendar"})
m = next(e for e in evs() if e["summary"] == "Mit Erinnerung")
call("PATCH", "/api/calendar/events", A, {"account": aid, "cal": AR, "href": m["href"], "etag": m["etag"], "summary": "Mit Erinnerung (neu)", "start": m["start"], "end": m["end"]})
raw = httpx.get(f"{AR}mit-alarm.ics", auth=AUTH).text
ok("Erinnerung + Kategorie bleiben beim Aendern erhalten", "BEGIN:VALARM" in raw and "TRIGGER:-PT30M" in raw and "CATEGORIES:Arzt" in raw and "Mit Erinnerung (neu)" in raw)
serie = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:t\r\nBEGIN:VEVENT\r\nUID:serie\r\nDTSTAMP:20261001T000000Z\r\nDTSTART:20261005T080000Z\r\nDTEND:20261005T083000Z\r\nRRULE:FREQ=WEEKLY;COUNT=10\r\nSUMMARY:Jour fixe\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
httpx.put(f"{AR}serie.ics", auth=AUTH, content=serie, headers={"Content-Type": "text/calendar"})
s = next(e for e in evs() if e["summary"] == "Jour fixe")
c, d = call("PATCH", "/api/calendar/events", A, {"account": aid, "cal": AR, "href": s["href"], "etag": s["etag"], "summary": "kaputt", "start": s["start"], "end": s["end"]})
ok("Serientermin aendern -> abgelehnt", c == 400 and "Serien" in d.get("error", ""))
ok("Serientermin loeschen -> abgelehnt", call("POST", "/api/calendar/events/delete", A, {"account": aid, "cal": AR, "href": s["href"], "etag": s["etag"]})[0] == 400)
ok("Serie bleibt vollstaendig", len([e for e in evs() if e["summary"] == "Jour fixe"]) == 4)
z2 = next(e for e in evs() if e["summary"].startswith("Zahnarzt"))
ok("Termin ueber falschen Kalender aendern -> abgelehnt", call("PATCH", "/api/calendar/events", A, {"account": aid, "cal": PR, "href": z2["href"], "etag": z2["etag"], "summary": "x", "start": z2["start"]})[0] == 400)
ok("Anderer Benutzer mit meinem Zugang -> 404", call("POST", "/api/calendar/events", X, {"account": aid, "cal": AR, "summary": "fremd", "start": "2026-10-14T08:30:00+02:00"})[0] == 404)
httpx.request("MKCALENDAR", f"{RAD}test/geheim/", auth=AUTH, content=MK.format("Geheim"))
ok("Nicht ausgewaehlter Kalender -> 404", call("POST", "/api/calendar/events", A, {"account": aid, "cal": f"{RAD}test/geheim/", "summary": "x", "start": "2026-10-14T08:30:00+02:00"})[0] == 404)
cn = sqlite3.connect(DB); cl = json.loads(cn.execute("SELECT calendars FROM cal_account WHERE id=?", (aid,)).fetchone()[0])
for x in cl: x["writable"] = x["name"] != "Privat"
cn.execute("UPDATE cal_account SET calendars=? WHERE id=?", (json.dumps(cl), aid)); cn.commit()
c, d = call("POST", "/api/calendar/events", A, {"account": aid, "cal": PR, "summary": "x", "start": "2026-10-14T08:30:00+02:00"}); ok(f"Nur-lesbarer Kalender -> {d.get('error')}", c == 400 and "lesbar" in d.get("error", ""))
ok("Unsinnige Zeit -> 400", call("POST", "/api/calendar/events", A, {"account": aid, "cal": AR, "summary": "x", "start": "morgen"})[0] == 400)
ok("Zeit ohne Zeitzone -> 400", call("POST", "/api/calendar/events", A, {"account": aid, "cal": AR, "summary": "x", "start": "2026-10-14T08:30"})[0] == 400)
ok("Loeschen", call("POST", "/api/calendar/events/delete", A, {"account": aid, "cal": AR, "href": z2["href"], "etag": next(e for e in evs() if e["summary"].startswith("Zahnarzt"))["etag"]})[0] == 200
   and not any(e["summary"].startswith("Zahnarzt") for e in evs()))
print(f"\nKALENDER-SCHREIBEN {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
cn.execute("DELETE FROM cal_account WHERE uid LIKE ?", ("hk-test-%",)); cn.commit(); sys.exit(0 if all(res) else 1)
