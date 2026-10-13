"""Test des CalDAV-Moduls gegen einen lokalen Radicale-Server (nur 127.0.0.1, http nur im Test erlaubt)."""
import os, sys, httpx
from datetime import datetime, timedelta, timezone
os.environ["CALDAV_ALLOWED_HOSTS"] = "127.0.0.1:5232"; os.environ["CALDAV_ALLOW_HTTP"] = "1"
import hkcal
B, AUTH = "http://127.0.0.1:5232/", ("test", os.environ.get("HK_RAD_PW", "pw")); res = []
def ok(n, c): res.append(bool(c)); print(("OK  " if c else "FAIL"), n)
def raises(fn, text=""):
    try: fn(); return False
    except hkcal.CalError as e: return text in str(e)
for c in ("arbeit", "privat"):
    httpx.request("MKCALENDAR", f"{B}test/{c}/", auth=AUTH, content=f'<?xml version="1.0"?><c:mkcalendar xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:set><d:prop><d:displayname>{c.title()}</d:displayname></d:prop></d:set></c:mkcalendar>')
cals = hkcal.discover(B, *AUTH); ok(f"Kalender gefunden: {[c['name'] for c in cals]}", sorted(c["name"] for c in cals) == ["Arbeit", "Privat"])
A = next(c["href"] for c in cals if c["name"] == "Arbeit")
tz = timezone(timedelta(hours=2)); t0 = datetime(2026, 10, 9, 14, 0, tzinfo=tz)
h1, e1 = hkcal.save_event(A, *AUTH, {"summary": "Frauenarzt", "start": t0.isoformat(), "end": (t0 + timedelta(hours=1)).isoformat(), "location": "Praxis"})
ok("Termin angelegt", h1.startswith(A) and e1)
hkcal.save_event(A, *AUTH, {"summary": "Urlaub", "allday": True, "start": "2026-10-20", "end": "2026-10-23"})
rr = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:test\r\nBEGIN:VEVENT\r\nUID:serie-1\r\nDTSTAMP:20261001T000000Z\r\nDTSTART:20261005T080000Z\r\nDTEND:20261005T083000Z\r\nRRULE:FREQ=WEEKLY;COUNT=10\r\nSUMMARY:Jour fixe\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
httpx.put(f"{A}serie-1.ics", auth=AUTH, content=rr, headers={"Content-Type": "text/calendar"})
ev = hkcal.events(A, *AUTH, datetime(2026, 10, 1, tzinfo=tz), datetime(2026, 11, 1, tzinfo=tz), tz)
ok(f"Termine im Oktober: {[(e['summary'], e['start'][:10]) for e in ev]}", len([e for e in ev if e["summary"] == "Jour fixe"]) == 4 and any(e["summary"] == "Frauenarzt" for e in ev))
u = next(e for e in ev if e["summary"] == "Urlaub"); ok("Ganztaegig erkannt (20.-23.)", u["allday"] and u["start"] == "2026-10-20" and u["end"] == "2026-10-23")
ok("Serie als Serie markiert", all(e["series"] for e in ev if e["summary"] == "Jour fixe"))
f = next(e for e in ev if e["summary"] == "Frauenarzt"); ok("Uhrzeit in Ortszeit", f["start"].startswith("2026-10-09T14:00") and f["location"] == "Praxis")
h2, e2 = hkcal.save_event(A, *AUTH, {"uid": f["uid"], "summary": "Frauenarzt (verschoben)", "start": (t0 + timedelta(days=1)).isoformat(), "end": (t0 + timedelta(days=1, hours=1)).isoformat()}, href=f["href"], etag=f["etag"])
ok("Aendern mit ETag", e2 and e2 != f["etag"])
ok("Aendern mit VERALTETEM ETag -> Konflikt", raises(lambda: hkcal.save_event(A, *AUTH, {"summary": "x", "start": t0.isoformat()}, href=f["href"], etag=f["etag"]), "inzwischen"))
P = next(c["href"] for c in cals if c["name"] == "Privat")
ok("Termin eines anderen Kalenders ueber diesen aendern -> abgelehnt", raises(lambda: hkcal.save_event(P, *AUTH, {"summary": "x", "start": t0.isoformat()}, href=h2, etag=e2), "gehört nicht"))
hkcal.delete_event(A, *AUTH, h2, e2)
ok("Loeschen", not any(e["summary"].startswith("Frauenarzt") for e in hkcal.events(A, *AUTH, datetime(2026, 10, 1, tzinfo=tz), datetime(2026, 11, 1, tzinfo=tz), tz)))
ok("Falsches Passwort -> verstaendliche Meldung", raises(lambda: hkcal.discover(B, "test", "falsch-" + AUTH[1]), "App-Passwort"))
ok("Nicht freigegebener Server -> abgelehnt", raises(lambda: hkcal.discover("https://evil.example/dav/", *AUTH), "nicht freigegeben"))
ok("Interne IP -> abgelehnt", raises(lambda: hkcal.discover("http://192.168.1.1/", *AUTH), "nicht freigegeben"))
ok("Antwort-Link auf fremden Server -> abgelehnt", raises(lambda: hkcal._href(A, type("E", (), {"text": "https://169.254.169.254/latest/"})()), "nicht freigegeben"))
ok("Zugangsdaten in der Adresse -> abgelehnt", raises(lambda: hkcal.check_url("http://a:b@127.0.0.1:5232/"), "Zugangsdaten"))
hkcal.SCHEMES = {"https"}; ok("Ohne Testfreigabe: http abgelehnt", raises(lambda: hkcal.check_url(B), "https"))
print(f"\nKALENDER-MODUL {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER"); sys.exit(0 if all(res) else 1)
