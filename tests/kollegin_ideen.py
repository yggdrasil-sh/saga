"""Kollegin K-D (2.6, Bugs-DB 2271): Tageszeile "Kurz gesagt" auf "Heute" (/api/ai/today, /api/app/today?line=1).

Ohne Server (immer):
- tests/today_vectors.json in sich stimmig (Format, Felder, v = Hash, Zeile/Ziele, 'show' aus der Antwort, Widget-Teil,
  RU-Plural) und Wort fuer Wort gleich dem heutigen Server-Wortlaut (tools/today_vectors.py check).
- Regeln (saga.ai_today.build/render, rein): <= 3 Teile in Prioritaet, (+n), <= 160 Grapheme, Titel <= 40 Grapheme mit "…",
  ganztaegig nur ohne Uhrzeit-Termin, vorbei zaehlt nicht, Abendregel ab 18:00, Tag in der Zone der Person (zwei Zonen,
  gleicher Zeitpunkt), Uhrzeit wie im Kalender (en-US 12 h), Satz hoechstens zwei Dinge.
Mit Server (HK_TEST_BASE, Testuhr SAGA_TEST_HOOKS=1 + SAGA_TEST_TOKEN, Fake-KI HK_FAKEAI_PORT, Fake-CalDAV KD_DAV_PORT):
- Vertrag: Felder wie 'normal', ETag/304, ohne Faehigkeit ai {enabled:false} (Schreiben 403), Bearer hk_/sga_/OAuth 403,
  ohne X-Saga 401, ohne Einwilligung nutzbar, /api/app/today ohne line=1 unveraendert, mit line=1 genau die Widget-Felder.
- Kalender nur aus dem Speicher: CalDAV 5 s verzoegert -> Antwort < 300 ms mit pending ['calendar'] (= Fall 'kalt'),
  danach ai.today.changed und Fall 'normal' WORT FUER WORT (Termin, 3 ueberfaellige, Zeile einer Aufgabenquelle, 1 Auftrag).
- Abendregel und Zeitzone ueber die Testuhr (21:30 -> morgen; 23:59 -> 00:01 Tageswechsel; ?tz vs. Zone des Kontos).
- Ruhe bis 05:00 fuer zwei Sitzungen (04:59 noch, 05:01 nicht mehr), "Wieder an", 3x X genau eine Frage, aus -> waiting bleibt.
- Rechte: geteilte Quelle 'assigned' nur eigene Zeilen, Kanarien von Ben (Zeile, Aufgabe) nie bei Anna, Freigabe entzogen ->
  weg, fremde Server (negative ID) nie Quelle.
- Live: ai.today.changed {v} bei neuer Aufgabe und bei Auftrag aus "Wartet auf dich" (job.state), v = GET in Konto-Sprache/
  -Zone, nur an die Person selbst.
- Stiller Widget-Push (widget_push.mark today) bei Ruhe/Schalter (im Prozess), Export "Meine Daten" mit Tageszeile.
- 0 Modellaufrufe (Fake-Zaehler), auch bei unerreichbarem Endpunkt.
Wegwerf-Konten kd-*; stellt KI-, Persona- und Host-Einstellungen und die Testuhr wieder her.
Aufruf (saga-test): PYTHONPATH=tests/pgshim:. python tests/kollegin_ideen.py [--nur-vektoren]"""
import base64
import hashlib
import http.client
import importlib.util
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VEC = json.load(open(os.path.join(HERE, "today_vectors.json"), encoding="utf-8"))
TARGETS = {"task": {"id"}, "row": {"id"}, "tasks": {"filter"}, "events": {"date"}, "jobs": {"group"}}
WIDGET = ("enabled", "on", "line", "waiting", "counts", "quiet", "v")
BER = ZoneInfo("Europe/Berlin")
res = []


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, "" if c else str(info)[:600], flush=True)


def case(i):
    return next(c for c in VEC["cases"] if c["id"] == i)


def at(s):
    return datetime.fromisoformat(s).timestamp()


# --- Vektoren (ohne Server) -----------------------------------------------------------------------------------------
def _v(d):
    d = {k: v for k, v in d.items() if k != "v"}
    return hashlib.sha256(json.dumps(d, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def _show(d):
    if not d.get("enabled"):
        return {"kurz": None, "pill": None, "ask": None, "waiting": None}
    ln = d.get("line")
    return {"kurz": {"label": d["label"], "parts": [p["text"] for p in ln["parts"]], "a11y": ln["a11y"]} if ln else None,
            "pill": {"text": d["quiet"]["text"], "undo": d["quiet"]["undo"]} if d.get("quiet") and not d.get("ask") else None,
            "ask": d.get("ask"), "waiting": d["waiting"]["text"] if d.get("waiting") else None}


def _target_ok(tg):
    return isinstance(tg, dict) and tg.get("type") in TARGETS and set(tg) == {"type"} | TARGETS[tg["type"]]


def _line_ok(ln):
    if ln is None:
        return True
    ps = ln.get("parts") or []
    return (set(ln) == {"text", "a11y", "parts"} and 1 <= len(ps) <= 3 and len(ln["text"]) <= 160
            and ln["text"] == " · ".join(p["text"] for p in ps)
            and all(p["kind"] in ("event", "over", "due", "tomorrow") and _target_ok(p["target"])
                    and (("n" not in p) or (p["n"] > 1 and p["text"].endswith(f"(+{p['n'] - 1})"))) for p in ps))


def vectors():
    ok("Vektoren: Format saga-today/v1 mit Kopf (note, fields, display, live)",
       VEC.get("format") == "saga-today/v1" and all(VEC.get(k) for k in ("note", "fields", "display", "live", "cases")))
    keys = {k for k in VEC["fields"] if "." not in k and k not in ("target", "a11y")} | {"enabled", "v"}
    ids = [c["id"] for c in VEC["cases"]]
    need = {"normal", "ruhig", "ruhe", "frage", "ohne_ai", "kalt", "sie", "th", "ru", "widget", "aus", "abend"}
    ok("Vektoren: alle verlangten Faelle da, ids eindeutig", need <= set(ids) and len(ids) == len(set(ids)), sorted(need - set(ids)))
    bad = []
    for c in VEC["cases"]:
        r = c["response"]
        if c["id"].startswith("widget"):
            part = (r or {}).get("line")
            if c["id"] == "widget_alt":
                bad += [c["id"]] if "line" in r else []
            elif part.get("enabled") is False:
                bad += [c["id"]] if part != {"enabled": False} else []
            elif set(part) != set(WIDGET) or not _line_ok(part["line"]):
                bad.append(c["id"])
            continue
        if c["status"] != 200 or r == {"enabled": False}:
            if c["show"] != _show({"enabled": False}) and c["id"] != "nicht_geaendert":
                bad.append(c["id"])
            continue
        if (set(r) != keys or r["v"] != _v(r) or not _line_ok(r["line"]) or c["show"] != _show(r)
                or (r["waiting"] and not _target_ok(r["waiting"]["target"]))
                or ((r["quiet"] or r["ask"] or not r["on"]) and r["line"] is not None)):
            bad.append(c["id"])
    ok("Vektoren: jede Antwort vollstaendig, v = Hash, Zeile/Ziele gueltig, 'show' folgt aus der Antwort", not bad, bad)
    th = case("th")["response"]
    ok("Vektoren: Thai-Teile ohne ค่ะ/ครับ, Satz mit Partikel",
       not any(x in p["text"] for p in th["line"]["parts"] for x in ("ค่ะ", "ครับ")) and "ค่ะ" in th["sentence"])
    ru = VEC["waiting_plural"]["ru"]["du"]
    ok("Vektoren: RU-Plural 1 / 2-4 / 5+ / 21", ru["1"].startswith("1 поручение") and ru["2"].startswith("2 поручения")
       and ru["5"].startswith("5 поручений") and ru["21"].startswith("21 поручение"))
    n = case("normal")
    ok("Vektoren: ETag = v, Live-Beispiel traegt v", n["headers"]["ETag"] == f'"{n["response"]["v"]}"'
       and VEC["live"]["data"]["v"] == n["response"]["v"])
    spec = importlib.util.spec_from_file_location("today_vectors", os.path.join(ROOT, "tools", "today_vectors.py"))
    tv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tv)
    probs = tv.check()
    ok(f"Vektoren = heutiger Server-Wortlaut (tools/today_vectors.py check, {len(probs)} Abweichungen)", not probs, probs[:5])


# --- Regeln (rein, ohne Datenbank) ----------------------------------------------------------------------------------
def rules():
    for k, v in (("SECRET_KEY", "kd-rules-" + "x" * 32), ("BASE_URL", "http://127.0.0.1:1")):
        os.environ.setdefault(k, v)
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from saga import ai_today as A
    from saga.ai_say import graphemes
    P = {"name": "Marin", "form": "sie", "lively": "warm", "address": "du"}
    now = at("2026-07-07T07:40:00+02:00")

    def F(kind, title, **kw):
        return A.Fact(kind, f"{kind}:{title}", 1, dict(title=title, **kw), {"type": "task", "id": 1} if kind in ("over", "due")
                      else {"type": "events", "date": "2026-07-07"})

    def b(fs, when=now, lang="de", tz=BER):
        return A.build(fs, now=when, tz=tz, lang=lang, persona=P, name="Anna")

    many = ([F("event", "Elternabend", time="19:30"), F("event", "Training", time="20:00")]
            + [F("over", f"Ü{i}", prio=1, due="2026-07-01", time="") for i in range(4)]
            + [F("due", "Kuchen", prio=1, due="2026-07-07", time=""), F("tomorrow", "Zahnarzt", time="08:00")])
    d = b(many)
    ok("Regel: hoechstens 3 Teile in Prioritaet (Termin, ueberfaellig, heute), (+n) und n",
       [p["kind"] for p in d["line"]["parts"]] == ["event", "over", "due"] and d["line"]["parts"][0]["text"] == "Elternabend 19:30 (+1)"
       and d["line"]["parts"][0]["n"] == 2 and d["line"]["parts"][1]["n"] == 4, d["line"])
    long = [F("event", "E" * 10 + " lange Veranstaltung mit sehr vielen Worten", time="19:30"),
            F("over", "Ü " * 30, prio=1, due="2026-07-01", time=""), F("due", "Heute " * 12, prio=1, due="2026-07-07", time="")]
    d = b(long)
    ps = d["line"]["parts"]
    ok(f"Regel: Titel <= 40 Grapheme mit „…“, Zeile <= 160 Grapheme ({graphemes(d['line']['text'])})",
       graphemes(d["line"]["text"]) <= 160 and all("…" in p["text"] for p in ps) and all(graphemes(p["text"]) < 60 for p in ps), d["line"])
    huge = [F("event", "X" * 39 + "y", time="19:30"), F("over", "Ä" * 39 + "y", prio=1, due="2026-07-01", time=""),
            F("due", "Ö" * 39 + "y", prio=1, due="2026-07-07", time="")]
    ok("Regel: drei Teile mit 40er-Titeln passen (Ueberlauf nur nach Prioritaet)", len(b(huge)["line"]["parts"]) == 3)
    thai = b([F("event", "ก" * 60, time="19:30")], lang="th")
    ok("Regel: Thai-Grapheme gezaehlt (Kuerzung trotz Vokalzeichen)", graphemes(thai["line"]["parts"][0]["text"]) <= 50, thai["line"])
    d = b([F("event", "Feiertag", allday=True), F("event", "Elternabend", time="19:30")])
    ok("Regel: ganztaegig nur ohne Uhrzeit-Termin", [p["text"] for p in d["line"]["parts"]] == ["Elternabend 19:30"], d["line"])
    d = b([F("event", "Feiertag", allday=True)])
    ok("Regel: nur ganztaegig -> „Feiertag (ganztägig)“", d["line"]["parts"][0]["text"] == "Feiertag (ganztägig)", d["line"])
    eve = at("2026-07-07T19:05:00+02:00")
    d = b([F("tomorrow", "Zahnarzt", time="08:00")], eve)
    d2 = b([F("tomorrow", "Zahnarzt", time="08:00")], at("2026-07-07T17:59:00+02:00"))
    d3 = b([F("event", "Kino", time="20:00"), F("tomorrow", "Zahnarzt", time="08:00")], eve)
    ok("Regel: ab 18:00 erster Termin von morgen, vorher nicht, nicht bei weiterem Termin heute",
       d["line"]["parts"][0]["text"] == "Morgen 08:00 Zahnarzt" and d2["line"] is None
       and [p["kind"] for p in d3["line"]["parts"]] == ["event"], (d["line"], d2["line"], d3["line"]))
    ok("Regel: Satz am Abend", d["sentence"] == "Guten Abend, Anna. Morgen: Zahnarzt um 08:00.", d["sentence"])
    ok("Regel: Uhrzeit wie im Kalender (de 24 h, en-US 12 h)", A.fmt_time("19:30", "de") == "19:30"
       and A.fmt_time("19:30", "en") == "07:30 PM" and A.fmt_time("00:05", "en") == "12:05 AM"
       and A.fmt_time("12:00", "en") == "12:00 PM")
    evs = [{"summary": "Abendessen", "start": "2026-07-07T23:30:00+02:00", "end": "2026-07-08T00:30:00+02:00", "allday": False},
           {"summary": "Vorbei", "start": "2026-07-07T06:00:00+02:00", "end": "2026-07-07T07:00:00+02:00", "allday": False},
           {"summary": "Urlaub", "start": "2026-07-06", "end": "2026-07-09", "allday": True}]
    t = at("2026-07-07T23:00:00+02:00")
    fb = A._event_facts(evs, datetime.fromtimestamp(t, BER))
    fn = A._event_facts(evs, datetime.fromtimestamp(t, ZoneInfo("America/New_York")))
    ok("Zonen: gleicher Zeitpunkt - Berlin 23:00 (Abendessen 23:30, Vorbei fehlt), New York 17:00 (Abendessen 17:30)",
       [f.fields["title"] for f in fb if f.kind == "event" and not f.fields.get("allday")] == ["Abendessen"]
       and [f.fields.get("time") for f in fn if f.kind == "event" and not f.fields.get("allday")] == ["17:30"]
       and any(f.fields.get("allday") and f.kind == "tomorrow" for f in fb), ([(f.kind, f.fields) for f in fb], [(f.kind, f.fields) for f in fn]))
    fb2 = A._event_facts(evs, datetime.fromtimestamp(at("2026-07-08T00:01:00+02:00"), BER))
    ok("Zonen: 00:01 - Termin, der gestern begann, zaehlt nicht als heute",
       not [f for f in fb2 if f.kind == "event" and not f.fields.get("allday")], [(f.kind, f.fields) for f in fb2])
    d = b([F("due", "C# lernen", prio=1, due="2026-07-07", time=""), F("over", "Plan_B", prio=1, due="2026-07-01", time="")])
    ok("2547: Titel mit # und _ unveraendert in Zeile und Satz", [p["text"] for p in d["line"]["parts"]] == ["„Plan_B“ überfällig", "„C# lernen“ heute"]
       and "„C# lernen“" in d["sentence"], d["line"])
    d = b([])
    ok("Ruhiger Tag: line null, waiting null, Satz 'Heute steht nichts an.'", d["line"] is None and d["waiting"] is None
       and d["sentence"] == "Guten Morgen, Anna. Heute steht nichts an.", d)
    d = b(many[:1] + many[2:3] + many[6:7])
    ok("Satz: hoechstens zwei Dinge", d["sentence"] == "Guten Morgen, Anna. Heute: Elternabend um 19:30 und „Ü0“ ist überfällig.", d["sentence"])


# --- Fake-CalDAV ------------------------------------------------------------------------------------------------------
class Dav:
    events, delay, reports = [], 0.0, 0


class DavH(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _user(self):
        try:
            u, pw = base64.b64decode(self.headers.get("Authorization", "").split(" ", 1)[1]).decode().split(":", 1)
        except Exception:  # noqa: BLE001
            return None
        return u if pw == "app-pw-" + u else None

    def _xml(self, body):
        b = body.encode()
        self.send_response(207)
        self.send_header("Content-Type", "application/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _deny(self):
        self.send_response(401)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_PROPFIND(self):
        u = self._user()
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if not u:
            return self._deny()
        ns = 'xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"'
        if self.headers.get("Depth") == "1":
            return self._xml(f'<?xml version="1.0"?><d:multistatus {ns}><d:response><d:href>/cal/{u}/main/</d:href><d:propstat><d:prop>'
                             f'<d:resourcetype><d:collection/><c:calendar/></d:resourcetype><d:displayname>Familie</d:displayname>'
                             f'</d:prop></d:propstat></d:response></d:multistatus>')
        return self._xml(f'<?xml version="1.0"?><d:multistatus {ns}><d:response><d:href>/</d:href><d:propstat><d:prop>'
                         f'<c:calendar-home-set><d:href>/cal/{u}/</d:href></c:calendar-home-set></d:prop></d:propstat></d:response></d:multistatus>')

    def do_REPORT(self):
        u = self._user()
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if not u:
            return self._deny()
        Dav.reports += 1
        time.sleep(Dav.delay)
        parts = []
        for uid, summ, s, e in Dav.events:
            ical = (f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:t\r\nBEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:20260701T000000Z\r\n"
                    f"DTSTART:{s}\r\nDTEND:{e}\r\nSUMMARY:{summ}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
            parts.append(f'<d:response><d:href>/cal/{u}/main/{uid}.ics</d:href><d:propstat><d:prop><d:getetag>"1"</d:getetag>'
                         f'<c:calendar-data>{ical}</c:calendar-data></d:prop></d:propstat></d:response>')
        self._xml('<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">' + "".join(parts) + "</d:multistatus>")


# --- Server-Helfer --------------------------------------------------------------------------------------------------
class H:
    B = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8920"))
    HOST = urllib.parse.urlparse(os.environ.get("BASE_URL", "http://127.0.0.1:8920")).netloc
    TOKEN = os.environ.get("SAGA_TEST_TOKEN", "")


def ck(u, lo=""):
    from itsdangerous import TimestampSigner
    S = TimestampSigner(os.environ["SESSION_SECRET"])
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5, "lo": lo}).encode())).decode()


def req(m, p, u=None, body=None, hdr=None, xs=True, lo="", lang="de"):
    c = http.client.HTTPConnection(H.B.hostname, H.B.port, timeout=30)
    h = {"Host": H.HOST, "Accept-Language": lang}
    if u:
        h["Cookie"] = "saga=" + ck(u, lo)
        if xs:
            h["X-Saga"] = "1"
    data = None
    if body is not None:
        h["Content-Type"] = "application/json"
        data = body if isinstance(body, str) else json.dumps(body)
    h.update(hdr or {})
    c.request(m, p, body=data, headers=h)
    r = c.getresponse()
    raw = r.read()
    hd = {k.lower(): v for k, v in r.getheaders()}
    try:
        out = json.loads(raw) if raw else None
    except ValueError:
        out = raw.decode(errors="replace")
    return r.status, out, hd


def clock(**b):
    return req("POST", "/_test/clock", body=b, hdr={"X-Saga-Test": H.TOKEN})


class Stream:
    """Live-Kanal (/api/events) mitlesen."""
    def __init__(self, u):
        self.c = http.client.HTTPConnection(H.B.hostname, H.B.port, timeout=120)
        self.c.request("GET", "/api/events", headers={"Host": H.HOST, "Accept": "text/event-stream", "Cookie": "saga=" + ck(u)})
        self.r = self.c.getresponse()
        self.ev = []
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        cur = {}
        try:
            while True:
                line = self.r.readline()
                if not line:
                    break
                line = line.decode("utf-8").rstrip("\r\n")
                if line == "":
                    if "event" in cur:
                        self.ev.append((cur["event"], json.loads(cur.get("data") or "null")))
                    cur = {}
                elif not line.startswith(":"):
                    k, _, v = line.partition(":")
                    cur[k] = v[1:] if v.startswith(" ") else v
        except Exception:  # noqa: BLE001
            pass

    def wait(self, typ, since, timeout=10.0, not_v=None):
        end = time.time() + timeout
        while time.time() < end:
            hit = [d for t_, d in self.ev[since:] if t_ == typ and (not_v is None or (d or {}).get("v") != not_v)]
            if hit:
                return hit[-1]
            time.sleep(0.1)
        return None

    def close(self):
        try:
            self.c.close()
        except Exception:  # noqa: BLE001
            pass


def same_as(got, want, ids=True):
    """Antwort gegen einen Vektor: alles gleich ausser v und (ohne ids) den Kennungen in den Zielen."""
    def strip(d):
        d = json.loads(json.dumps(d))
        d.pop("v", None)
        for p in ((d.get("line") or {}).get("parts") or ()):
            if not ids:
                p["target"].pop("id", None)
        return d
    a, b = strip(got or {}), strip(want or {})
    diff = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
    return not diff, {k: (a.get(k), b.get(k)) for k in diff}


# --- Server -----------------------------------------------------------------------------------------------------------
def server():
    import auth
    import db
    db.wait_ready()
    from db import connect
    from saga.core import _tok_hash, local_ref

    A, BEN, OFF = "kd-anna", "kd-ben", "kd-aus"
    FAKE_PORT = int(os.environ.get("HK_FAKEAI_PORT", "8925"))
    DAV_PORT = int(os.environ.get("KD_DAV_PORT", "8926"))
    FAKE = f"http://127.0.0.1:{FAKE_PORT}"

    def q(sql, args=()):
        with connect() as c, c:
            cur = c.execute(sql, args)
            try:
                return cur.fetchall()
            except Exception:  # noqa: BLE001
                return []

    def fake_n():
        try:
            c = http.client.HTTPConnection("127.0.0.1", FAKE_PORT, timeout=5)
            c.request("GET", "/_count")
            return json.loads(c.getresponse().read()).get("n", -1)
        except OSError:
            return -1

    def cleanup():
        from saga.userdata import delete_user_data
        for u in [r[0] for r in q("SELECT username FROM users WHERE username LIKE 'kd-%'")]:
            delete_user_data(u)
        with connect() as c, c:
            for t_ in ("job", "user_kv", "task", "app_token", "cal_account", "task_source", "note"):
                c.execute(f"DELETE FROM {t_} WHERE uid LIKE 'kd-%'")
            c.execute("DELETE FROM users WHERE username LIKE 'kd-%'")

    def get(u=A, tz="Europe/Berlin", **kw):
        return req("GET", "/api/ai/today" + (f"?tz={tz}" if tz else ""), u, **kw)

    st0 = auth.settings(fresh=True)
    prev = {k: st0.get(k) for k in ("ai", "ai_persona", "allowed_hosts")}
    cleanup()
    proc = subprocess.Popen([sys.executable, os.path.join(HERE, "fakeai.py"), str(FAKE_PORT)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    dav = ThreadingHTTPServer(("127.0.0.1", DAV_PORT), DavH)
    threading.Thread(target=dav.serve_forever, daemon=True).start()
    streams = []
    try:
        for _ in range(80):
            try:
                socket.create_connection(("127.0.0.1", FAKE_PORT), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        for u, name in ((A, "Anna"), (BEN, "Ben"), (OFF, "Clara")):
            auth.create_user(u, "Kd-Pass-12345", name)
        for u in (A, BEN):
            auth.set_user_groups(auth.get_user(u)["id"], ["kd-familie"])
        q("INSERT INTO user_kv (uid, k, v) VALUES (?, 'lang', 'de')", (A,))
        ai = dict(prev["ai"] or {})
        ai.update(enabled=True, provider="openai", url=FAKE + "/v1", model="fake", groups=["kd-familie"], pinned=["127.0.0.1"])
        auth.save_setting("ai", ai)
        auth.save_setting("ai_persona", {"name": "Marin", "tone": ""})
        auth.save_setting("allowed_hosts", ((prev["allowed_hosts"] or "") + f", 127.0.0.1:{DAV_PORT}").strip(", "))
        auth.settings(fresh=True)
        time.sleep(5.6)                                   # Einstellungen sind im Server 5 s zwischengespeichert
        n_model = fake_n()
        tok = "hk_" + secrets.token_urlsafe(24)
        q("INSERT INTO app_token (uid, name, hash, prefix, created) VALUES (?, 'kd', ?, 'hk_', ?)", (A, _tok_hash(tok), time.time()))
        tok_off = "hk_" + secrets.token_urlsafe(24)
        q("INSERT INTO app_token (uid, name, hash, prefix, created) VALUES (?, 'kd', ?, 'hk_', ?)", (OFF, _tok_hash(tok_off), time.time()))
        hk = {"Authorization": "Bearer " + tok}

        # --- A. Vertrag (Echtzeit) ----------------------------------------------------------------------------------
        today = datetime.now(BER).date().isoformat()
        q("INSERT INTO task (uid, text, due, prio, done, created) VALUES (?, 'Pavillon abholen', '2020-01-01', 1, 0, ?)", (A, time.time()))
        q("INSERT INTO task (uid, text, due, prio, done, created) VALUES (?, 'Kuchen backen', ?, 2, 0, ?)", (A, today, time.time()))
        want = set(case("normal")["response"])
        st, d, h = get()
        ok("GET: 200 mit genau den Feldern von Fall 'normal', ohne Einwilligung", st == 200 and set(d) == want and d["enabled"], (st, d))
        ok("GET: ETag = v, tz uebernommen, Zeile mit Aufgaben", h.get("etag") == f'"{d.get("v")}"' and d.get("tz") == "Europe/Berlin"
           and d.get("line") and [p["kind"] for p in d["line"]["parts"]] == ["over", "due"], (h.get("etag"), d))
        ok("GET: Zeile/Ziele in Vertragsform", _line_ok(d.get("line")), d.get("line"))
        st2, d2, _ = get(hdr={"If-None-Match": h.get("etag", "")})
        ok("GET: If-None-Match -> 304 ohne Inhalt", st2 == 304 and not d2, (st2, d2))
        st, d, _ = get(tz="Nirgendwo/Stadt")
        ok("GET: unbekannte Zone -> Zone des Kontos", st == 200 and d.get("tz") != "Nirgendwo/Stadt", d.get("tz"))
        st, d, _ = get(OFF)
        ok("Ohne Faehigkeit ai: 200 {enabled:false}", st == 200 and d == {"enabled": False}, (st, d))
        ok("Ohne Faehigkeit ai: Schreiben 403", req("POST", "/api/ai/today/quiet", OFF)[0] == 403
           and req("PUT", "/api/ai/today", OFF, {"on": False})[0] == 403 and req("DELETE", "/api/ai/today/quiet", OFF)[0] == 403)
        ok("Bearer hk_ an /api/ai/today -> 403", req("GET", "/api/ai/today", hdr=hk)[0] == 403)
        ok("Bearer sga_ / OAuth an /api/ai/today* -> 403",
           req("POST", "/api/ai/today/quiet", hdr={"Authorization": "Bearer sga_" + "x" * 40})[0] == 403
           and req("PUT", "/api/ai/today", body={"on": False}, hdr={"Authorization": "Bearer sgo_" + "y" * 40})[0] == 403)
        ok("Ohne X-Saga -> 401", get(xs=False)[0] == 401)
        st, w0, _ = req("GET", "/api/app/today", hdr=hk)
        st1, w1, _ = req("GET", "/api/app/today?line=1&tz=Europe/Berlin", hdr=hk)
        st3, w3, _ = req("GET", "/api/app/today?sources=1", hdr=hk)
        ok("Widget ohne line=1: kein Feld 'line' (auch mit sources=1)", st == 200 and "line" not in w0 and st3 == 200 and "line" not in w3, w0)
        ok("Widget mit line=1: genau die Widget-Felder", st1 == 200 and set(w1.get("line") or {}) == set(WIDGET)
           and set(w1) - {"line"} == set(w0), w1)
        st, w2, _ = req("GET", "/api/app/today?line=1", hdr={"Authorization": "Bearer " + tok_off})
        ok("Widget ohne Faehigkeit ai: line {enabled:false}", st == 200 and w2.get("line") == {"enabled": False}, w2)
        bad = [b for b in ({"on": "nein"}, {"foo": 1}, {}, {"asked": 1}, "[]", "kaputt") if req("PUT", "/api/ai/today", A, b)[0] != 400]
        ok("PUT mit falschen Werten -> 400, nichts geschrieben", not bad and not q("SELECT v FROM user_kv WHERE uid=? AND k='ai_today'", (A,)), bad)
        q("DELETE FROM task WHERE uid=?", (A,))

        # --- B. Kalender kalt -> normal, Wort fuer Wort (Testuhr 07.07.2026 07:40) -------------------------------------
        st, _c, _ = clock(freeze=True, set=at("2026-07-07T07:40:00+02:00"))
        ok("Testuhr erreichbar (SAGA_TEST_HOOKS/SAGA_TEST_TOKEN)", st == 200, st)
        Dav.events = [("kd1", "Elternabend", "20260707T173000Z", "20260707T190000Z"),
                      ("kd2", "Zahnarzt", "20260708T060000Z", "20260708T063000Z")]
        Dav.delay, Dav.reports = 5.0, 0                  # ab jetzt antwortet der Kalender erst nach 5 s
        cal = f"http://127.0.0.1:{DAV_PORT}/"
        s1, r1, _ = req("POST", "/api/settings/calendars/test", A, {"base_url": cal, "username": A, "password": "app-pw-" + A})
        cals = r1.get("calendars", []) if isinstance(r1, dict) else []
        s2, r2, _ = req("POST", "/api/settings/calendars", A, {"base_url": cal, "username": A, "password": "app-pw-" + A,
                                                             "calendars": [dict(x, show=True) for x in cals]})
        ok(f"Kalender eingerichtet ({s1}, {s2}, {len(cals)})", s1 == 200 and s2 in (200, 201) and cals, (r1, r2))
        q("INSERT INTO task (uid, text, due, prio, done, created) VALUES (?, 'Pavillon abholen', '2026-07-05', 1, 0, 1)", (A,))
        q("INSERT INTO task (uid, text, due, prio, done, created) VALUES (?, 'Rest 0', '2026-07-06', 2, 0, 2)", (A,))
        q("INSERT INTO task (uid, text, due, prio, done, created) VALUES (?, 'Rest 1', '2026-07-06', 2, 0, 3)", (A,))
        dbid = req("POST", "/api/notes", A, {"kind": "database", "title": "Sommerfest"})[1]["id"]
        req("PATCH", f"/api/notes/{dbid}", A, {"schema": {"props": [{"id": "ok", "name": "Erledigt", "type": "checkbox"},
                                                                  {"id": "d", "name": "Datum", "type": "date"}],
                                                        "views": [{"id": "a", "name": "A", "type": "table"}], "tasks": {"done": "ok", "due": "d"}}})
        req("PUT", f"/api/notes/{dbid}/tasksource", A, {})
        row = req("POST", "/api/notes", A, {"parent_id": dbid, "title": "Kuchen backen", "props": {"d": "2026-07-07"}})[1]["id"]
        jid = q("INSERT INTO job (uid, kind, source, state, title, created, updated) VALUES (?, 'ai.task', 'saga', 'wartet_ok', "
                "'Grillkohle besorgen', ?, ?) RETURNING id", (A, at("2026-07-07T07:00:00+02:00"), at("2026-07-07T07:00:00+02:00")))[0][0]
        sa = Stream(A)
        streams.append(sa)
        time.sleep(0.3)
        mark = len(sa.ev)
        t0 = time.time()
        st, d, _ = get()
        ms = (time.time() - t0) * 1000
        ok(f"kalt: Antwort ohne Netz in {ms:.0f} ms (< 300) mit pending ['calendar'], counts.events null",
           st == 200 and ms < 300 and d.get("pending") == ["calendar"] and d["counts"]["events"] is None, (ms, d))
        good, diff = same_as(d, case("kalt")["response"], ids=False)
        ok("kalt: Wort fuer Wort wie Vektor 'kalt'", good, diff)
        ev = sa.wait("ai.today.changed", mark, 15, not_v=d.get("v"))     # nicht das Ereignis der neuen Zeile (noch kalt)
        ok(f"Kalender fertig -> ai.today.changed {{v}} ({ev})", isinstance(ev, dict) and re.fullmatch(r"[0-9a-f]{12}", str(ev.get("v"))), ev)
        st, d, h = get()
        good, diff = same_as(d, case("normal")["response"], ids=False)
        ok("normal: Wort fuer Wort wie Vektor 'normal' (Termin, 3 ueberfaellig, Zeile, 1 Auftrag)", st == 200 and good, diff)
        tg = [p["target"] for p in (d.get("line") or {}).get("parts") or ()]
        ok("normal: Ziele = Termine des Tages, die wichtigste Aufgabe, die Zeile der Quelle",
           len(tg) == 3 and tg[0] == {"type": "events", "date": "2026-07-07"} and tg[1]["type"] == "task" and tg[2] == {"type": "row", "id": row}, tg)
        from saga import tasksrc
        from datetime import date as _date
        with connect() as c:
            full = tasksrc.collect(c, A, _date(2026, 7, 7), BER, clean=False)[0]
            lean = tasksrc.collect(c, A, _date(2026, 7, 7), BER, clean=False, until="2026-07-08")[0]
        want_k = [x["key"] for x in full if x.get("due") and x["due"] <= "2026-07-08"]
        ok(f"tasksrc.collect(until): dieselben Eintraege wie der volle Weg, nur mit Datum bis until ({len(lean)}/{len(full)})",
           [x["key"] for x in lean] == want_k and all(x["overdue"] == y["overdue"] for x, y in zip(lean, [f for f in full if f["key"] in want_k])),
           ([x["key"] for x in lean], want_k))
        st, d0, _ = get(tz=None)
        ok("Live: v im Ereignis = GET in Konto-Sprache und -Zone", ev and d0.get("v") == ev.get("v"), (ev, d0.get("v")))
        ok(f"Kalender einmal abgefragt, im Hintergrund - auch bei mehreren kalten Abrufen ({Dav.reports})", Dav.reports == 1, Dav.reports)
        Dav.delay = 0.0

        # --- C. Abend, Tageswechsel, Zone --------------------------------------------------------------------------------
        clock(set=at("2026-07-07T21:30:00+02:00"))
        st, d, _ = get()
        ok("Abend (21:30, Elternabend vorbei): Zeile wie Vektor 'abend' + Aufgaben", d.get("line") and d["line"]["parts"][-1]
           == case("abend")["response"]["line"]["parts"][0] and d["counts"]["events"] == 0, d.get("line"))
        clock(set=at("2026-07-07T23:59:00+02:00"))
        st, d, _ = get()
        clock(set=at("2026-07-08T00:01:00+02:00"))
        st2, d2, _ = get()
        ok("23:59 -> 00:01: Tag wechselt, „Kuchen backen“ wird ueberfaellig (Testuhr)", d["day"] == "2026-07-07" and d2["day"] == "2026-07-08"
           and d["counts"]["due"] == 1 and d2["counts"]["over"] == 4 and d2["counts"]["due"] == 0, (d["counts"], d2["counts"]))
        st, dn, _ = get(tz="America/New_York")
        ok("Gleicher Zeitpunkt in New York: noch der 07.07.", dn["day"] == "2026-07-07" and dn["tz"] == "America/New_York", dn.get("day"))
        req("PUT", "/api/tasks/digest", A, {"tz": "America/New_York"})
        st, dk, _ = get(tz=None)
        ok("Ohne ?tz: Zone des Kontos", dk.get("tz") == "America/New_York" and dk["day"] == "2026-07-07", dk.get("tz"))
        req("PUT", "/api/tasks/digest", A, {"tz": ""})

        # --- D. Ruhe, Frage, aus -------------------------------------------------------------------------------------------
        clock(set=at("2026-07-07T08:15:00+02:00"))
        st, d, _ = req("POST", "/api/ai/today/quiet?tz=Europe/Berlin", A, lo="geraet-1")
        ok("✕: Ruhe bis 05:00 Ortszeit, line null, keine Frage", st == 200 and d["line"] is None and d["ask"] is None
           and (d["quiet"] or {}).get("until") == "2026-07-08T05:00:00+02:00", d.get("quiet"))
        good, diff = same_as(d, case("ruhe")["response"], ids=False)
        ok("Ruhe: Wort fuer Wort wie Vektor 'ruhe'", good, diff)
        st, d2, _ = get(lo="geraet-2")
        ok("Ruhe gilt fuer die zweite Sitzung", d2.get("quiet") and d2["line"] is None)
        clock(set=at("2026-07-08T04:59:00+02:00"))
        ok("04:59: noch Ruhe", get()[1].get("quiet") is not None)
        clock(set=at("2026-07-08T05:01:00+02:00"))
        st, d, _ = get()
        ok("05:01: Ruhe vorbei, Zeile wieder da", d.get("quiet") is None and d.get("line"), d.get("quiet"))
        clock(set=at("2026-07-07T08:15:00+02:00"))
        q("DELETE FROM user_kv WHERE uid=? AND k IN ('ai_today', 'ai_quiet')", (A,))
        req("POST", "/api/ai/today/quiet", A)
        st, d, _ = req("DELETE", "/api/ai/today/quiet", A)
        ok("Wieder an: quiet null, Zeile wieder da", st == 200 and d["quiet"] is None and d["line"], d.get("quiet"))
        req("POST", "/api/ai/today/quiet", A)
        st, d, _ = req("POST", "/api/ai/today/quiet?tz=Europe/Berlin", A)
        ok("Drittes ✕ in 14 Tagen: genau eine Frage", d.get("ask") and set(d["ask"]) == {"text", "yes", "no"} and d["quiet"], d)
        ok("Frage: Wortlaut wie Vektor 'frage'", d.get("ask") == case("frage")["response"]["ask"], d.get("ask"))
        st, d, _ = req("PUT", "/api/ai/today", A, {"asked": True})
        ok("Frage 'Nein, passt': ask null, Ruhe bleibt", st == 200 and d["ask"] is None and d["quiet"], d)
        req("DELETE", "/api/ai/today/quiet", A)
        req("POST", "/api/ai/today/quiet", A)
        st, d, _ = req("POST", "/api/ai/today/quiet", A)
        ok("Danach nie wieder gefragt", d["ask"] is None and d["quiet"], d)
        st, d, _ = req("PUT", "/api/ai/today", A, {"on": False, "asked": True})
        ok("Aus: on false, line/quiet null, 'Wartet auf dich' bleibt", st == 200 and d["on"] is False and d["line"] is None
           and d["quiet"] is None and (d["waiting"] or {}).get("n") == 1, d)
        st, d, _ = req("PUT", "/api/ai/today", A, {"on": True})
        ok("Wieder ein: Zeile da, Ruhe aufgehoben", st == 200 and d["on"] and d["line"] and d["quiet"] is None, d.get("quiet"))
        st, mdj, _ = req("GET", "/api/ai/mydata?format=json", A)
        st2, mdm, _ = req("GET", "/api/ai/mydata?format=md", A)
        td = mdj.get("today") if isinstance(mdj, dict) else None
        ok("Meine Daten: Tageszeile im Export (JSON und Markdown)", st == 200 and td and td["on"] is True and len(td["dismissed"]) == 3
           and td["asked"] is True and isinstance(mdm, str) and "## Tageszeile" in mdm, (st, td))
        q("DELETE FROM user_kv WHERE uid=? AND k IN ('ai_today', 'ai_quiet')", (A,))

        # --- E. Rechte -------------------------------------------------------------------------------------------------
        bdb = req("POST", "/api/notes", BEN, {"kind": "database", "title": "Ben Liste"})[1]["id"]
        req("PATCH", f"/api/notes/{bdb}", BEN, {"schema": {"props": [{"id": "ok", "name": "Erledigt", "type": "checkbox"},
                                                                   {"id": "d", "name": "Datum", "type": "date"},
                                                                   {"id": "wer", "name": "Wer", "type": "person"}],
                                                         "views": [{"id": "a", "name": "A", "type": "table"}],
                                                         "tasks": {"done": "ok", "due": "d", "assignee": "wer"}}})
        req("PUT", f"/api/notes/{bdb}/shares", BEN, {"type": "user", "target": local_ref(A), "role": "view"})
        req("POST", "/api/notes", BEN, {"parent_id": bdb, "title": "Für Anna", "props": {"d": "2026-07-07", "wer": [local_ref(A)]}})
        req("POST", "/api/notes", BEN, {"parent_id": bdb, "title": "Kanarie-Ben-Zeile", "props": {"d": "2026-07-07", "wer": [local_ref(BEN)]}})
        q("INSERT INTO task (uid, text, due, prio, done, created) VALUES (?, 'Kanarie-Ben-Aufgabe', '2026-07-07', 1, 0, 1)", (BEN,))
        s, sub, _ = req("PUT", f"/api/notes/{bdb}/tasksource", A, {})
        st, d, _ = get()
        ok(f"Geteilte Quelle ('{(sub or {}).get('scope')}'): nur die zugewiesene Zeile zaehlt mit",
           s == 200 and (sub or {}).get("scope") == "assigned" and d["counts"]["due"] == 2, (sub, d.get("counts")))
        ok("Kanarien von Ben (Zeile, Eingang) nie bei Anna", "Kanarie" not in json.dumps(d, ensure_ascii=False))
        req("DELETE", f"/api/notes/{bdb}/shares", BEN, {"type": "user", "target": local_ref(A)})
        st, d, _ = get()
        ok("Freigabe entzogen: Zeile weg", d["counts"]["due"] == 1 and "Für Anna" not in json.dumps(d, ensure_ascii=False), d.get("counts"))
        s, _x, _ = req("PUT", "/api/notes/-5/tasksource", A, {})
        ok(f"Fremder Server (negative ID) wird nie Quelle ({s})", s in (400, 404))

        # --- F. Live: Aufgabe, Auftrag aus "Wartet auf dich" -----------------------------------------------------------------
        mark = len(sa.ev)
        req("POST", "/api/tasks", A, {"text": "Neu heute", "due": "2026-07-07"})
        ev = sa.wait("ai.today.changed", mark, 6)
        time.sleep(0.6)
        st, d, _ = get(tz=None)
        ok("Neue Aufgabe -> ai.today.changed mit neuem v", ev and ev.get("v") == d.get("v"), (ev, d.get("v")))
        mark = len(sa.ev)
        s, _x, _ = req("POST", f"/api/ai/jobs/{jid}/cancel", A, {})
        ev = sa.wait("ai.today.changed", mark, 6)
        time.sleep(0.6)
        st, d, _ = get(tz=None)
        ok(f"Auftrag abgebrochen ({s}) -> job.state -> ai.today.changed, waiting null",
           ev and d["waiting"] is None and ev.get("v") == d.get("v"), (s, ev, d.get("waiting")))
        sb = Stream(BEN)
        streams.append(sb)
        time.sleep(0.5)
        mark_b = len(sb.ev)
        req("POST", "/api/tasks", A, {"text": "Noch eine", "due": "2026-07-07"})
        time.sleep(1.5)
        ok("Ereignis nur an die Person selbst (Ben bekommt nichts)", not [e for e in sb.ev[mark_b:] if e[0] == "ai.today.changed"])

        # --- G. Stiller Widget-Push (im Prozess) ---------------------------------------------------------------------------
        from saga import ai_today, widget_push
        calls, orig = [], widget_push.mark
        widget_push.mark = lambda c, uid, ids=(), today=False, every=False: calls.append((uid, today))
        try:
            ai_today.quiet(A, True, "Europe/Berlin")
            ai_today.quiet(A, False, "Europe/Berlin")
            ai_today.set_prefs(A, {"on": False})
            ai_today.set_prefs(A, {"on": True})
        finally:
            widget_push.mark = orig
        ok(f"Stiller Widget-Push bei Ruhe/Schalter ({len(calls)})", calls == [(A, True)] * 4, calls)
        q("DELETE FROM user_kv WHERE uid=? AND k IN ('ai_today', 'ai_quiet')", (A,))

        # --- H. 0 Modellaufrufe, auch bei unerreichbarem Endpunkt ---------------------------------------------------------
        ok(f"0 Modellaufrufe in allen Tageszeilen-Abrufen ({n_model} -> {fake_n()})", n_model >= 0 and fake_n() == n_model)
        auth.save_setting("ai", dict(ai, url="http://127.0.0.1:9/v1"))
        auth.settings(fresh=True)
        time.sleep(5.6)
        t0 = time.time()
        st, d, _ = get()
        ok(f"Unerreichbarer Endpunkt: Tageszeile unveraendert da ({(time.time() - t0) * 1000:.0f} ms)", st == 200 and d.get("enabled") and d.get("line"))
    finally:
        for s_ in streams:
            s_.close()
        clock(reset=True)
        proc.terminate()
        dav.shutdown()
        for k, v in prev.items():
            auth.save_setting(k, v if v is not None else ("" if k == "allowed_hosts" else {}))
        auth.settings(fresh=True)
        cleanup()


if __name__ == "__main__":
    vectors()
    rules()
    if "--nur-vektoren" not in sys.argv:
        server()
    print(f"\nKollegin Tageszeile {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)
