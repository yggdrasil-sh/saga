"""2.5 Paket 7 (P7-E4): taegliche Zusammenfassung "heute faellig / ueberfaellig" (Mitteilung task.due, saga/tasksrc.py).

Geprueft: Einstellung GET/PUT /api/tasks/digest (Standard aus, Uhrzeit, Zeitzone; ungueltige Werte 400), aus -> nichts,
an -> vor der Uhrzeit nichts, danach genau eine Mitteilung je Tag (zweiter Lauf am selben Tag nichts, naechster Tag
wieder), nichts faellig -> keine Mitteilung, Zeitzone des Kontos bestimmt "heute" (Inbox und Aufgabenquellen), Text in
der Glocke (Zahlen, kein Aufgabentext), Push ueber ntfy (Fake-Empfaenger, ohne Namen/Inhalte), Bereich "tasks" in den
Push-Einstellungen abschaltbar, Fremde sehen nichts. Ruft tasksrc.digest_run(now=...) in diesem Prozess auf. Der Server
sollte mit SAGA_TASKDUE_TICK=86400 laufen (sein eigener Takt verschickt sonst mitten im Test nach der echten Uhrzeit).
Wegwerf-Konten az-*."""
import base64
import http.client
import http.server
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from itsdangerous import TimestampSigner

import auth
from db import connect
from saga import push, tasksrc  # noqa: F401 - push meldet sich am Bus an (Auftraege fuer task.due)

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
FP = 18292
FAKE = f"http://127.0.0.1:{FP}"
GOT, LOCK = [], threading.Lock()
res = []
A, B = "az-" + secrets.token_hex(6), "az-" + secrets.token_hex(6)


def ok(n, c, detail=None):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, flush=True)
    if not c and detail is not None:
        print("     ", str(detail)[:1200], flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, u=A, body=None):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=60)
    h = {"Host": HOST, "Accept-Language": "de", "X-Saga": "1", "Cookie": "saga=" + ck(u)}
    data = None
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    c.request(m, p, body=data, headers=h)
    r = c.getresponse()
    raw = r.read()
    try:
        return r.status, json.loads(raw) if raw[:1] in (b"[", b"{") else raw
    except ValueError:
        return r.status, raw


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("content-length") or 0))
        with LOCK:
            GOT.append(body)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


def notes(u=A):
    with connect() as c:
        return c.execute("SELECT id, data FROM notification WHERE uid=? AND kind='task.due' ORDER BY id", (u,)).fetchall()


def pick_zone():
    """Zeitzone, in der es gerade zwischen 01:00 und 18:00 ist (Uhrzeit = jetzt + 2 h liegt noch am selben Tag)."""
    now = time.time()
    for z in ("Europe/Berlin", "America/New_York", "Asia/Tokyo", "America/Los_Angeles", "Asia/Kolkata", "Pacific/Auckland", "UTC"):
        h = datetime.fromtimestamp(now, ZoneInfo(z)).hour
        if 1 <= h < 18:
            return z
    return "UTC"


def main():
    cleanup()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", FP), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    st = auth.settings(fresh=True)
    keep = {k: st.get(k) for k in ("allowed_hosts", "push")}
    auth.save_setting("allowed_hosts", ", ".join(x for x in (keep["allowed_hosts"], f"127.0.0.1:{FP}") if x))
    auth.save_setting("push", {"enabled": True, "webpush": True, "ntfy": True, "unifiedpush": True})
    auth.create_user(A, "Digest-Pass-1234", "Anna Digest")
    auth.create_user(B, "Digest-Pass-1234", "Bert Digest")
    time.sleep(5.6)
    try:
        run()
    finally:
        for k, v in keep.items():
            if v is not None:
                auth.save_setting(k, v)
        cleanup()
        srv.shutdown()


def run():
    s, d = req("GET", "/api/tasks/digest")
    ok(f"Standard: aus, 07:30, Zeitzone leer ({s})", s == 200 and d == {"on": False, "time": "07:30", "tz": ""}, d)
    for body in ({"on": "ja"}, {"time": "25:00"}, {"tz": "Mars/Olymp"}, {"x": 1}):
        s, _ = req("PUT", "/api/tasks/digest", body=body)
        ok(f"ungueltig {body} -> 400 ({s})", s == 400)
    z = pick_zone()
    tz = ZoneInfo(z)
    now = time.time()
    loc = datetime.fromtimestamp(now, tz)
    at = (loc + timedelta(hours=2)).strftime("%H:%M")
    today = loc.date()
    s, d = req("PUT", "/api/tasks/digest", body={"time": at, "tz": z})
    ok(f"Uhrzeit/Zeitzone gespeichert, weiter aus ({s})", s == 200 and d == {"on": False, "time": at, "tz": z}, d)
    # Daten: Inbox heute faellig, Inbox ueberfaellig, Quelle (Haekchen) heute faellig, morgen faellig
    req("POST", "/api/tasks", body={"text": "Geheimer Inbox-Text", "due": today.isoformat()})
    req("POST", "/api/tasks", body={"text": "Alt", "due": (today - timedelta(days=3)).isoformat()})
    req("POST", "/api/tasks", body={"text": "Morgen", "due": (today + timedelta(days=1)).isoformat()})
    db = req("POST", "/api/notes", body={"kind": "database", "title": "Liste"})[1]["id"]
    req("PATCH", f"/api/notes/{db}", body={"schema": {"props": [{"id": "ok", "name": "Erledigt", "type": "checkbox"},
                                                               {"id": "d", "name": "Datum", "type": "date"}],
                                                     "views": [{"id": "a", "name": "A", "type": "table"}], "tasks": {"done": "ok", "due": "d"}}})
    req("PUT", f"/api/notes/{db}/tasksource", body={})
    req("POST", "/api/notes", body={"parent_id": db, "title": "Geheime Zeile", "props": {"d": today.isoformat()}})
    s, _ = req("POST", "/api/push/subscriptions", body={"type": "ntfy", "url": f"{FAKE}/az-topic", "name": "Handy"})
    ok(f"ntfy-Abo ({s})", s == 201)

    ok("aus: kein Lauf schickt etwas", tasksrc.digest_run(now + 3 * 3600) == 0 and not notes())
    req("PUT", "/api/tasks/digest", body={"on": True})
    ok("an, vor der Uhrzeit: nichts", tasksrc.digest_run(now + 3600) == 0 and not notes())
    n0 = len(GOT)
    sent = tasksrc.digest_run(now + 2 * 3600 + 120)
    ns = notes()
    ok(f"an, nach der Uhrzeit: genau eine Mitteilung ({sent})", sent == 1 and len(ns) == 1, ns)
    dd = json.loads(ns[0][1]) if ns else {}
    ok(f"Inhalt: 2 heute faellig (Inbox + Quelle), 1 ueberfaellig, Tag in der Kontozeitzone ({dd})",
       dd.get("due") == 2 and dd.get("overdue") == 1 and dd.get("day") == today.isoformat(), dd)
    ok("zweiter Lauf am selben Tag: nichts", tasksrc.digest_run(now + 2 * 3600 + 600) == 0 and len(notes()) == 1)
    s, d = req("GET", "/api/notifications")
    it = [x for x in d.get("items", []) if x["kind"] == "task.due"]
    ok(f"Glocke: Text mit Zahlen, ohne Aufgabentexte ({it[0]['text'] if it else None})", it and it[0]["text"] == "Heute fällig: 2 · überfällig: 1"
       and it[0]["access"] and "Geheim" not in json.dumps(it), it)
    end = time.time() + 15
    while time.time() < end and len(GOT) <= n0:
        time.sleep(0.2)
    with LOCK:
        got = [json.loads(x) for x in GOT[n0:]]
    ok(f"Push ueber ntfy angekommen, ohne Namen/Inhalte ({got[:1]})", got and got[0]["topic"] == "az-topic"
       and got[0]["message"] == "Aufgaben heute fällig oder überfällig" and "Geheim" not in json.dumps(got), got)
    # naechster Tag: wieder (Inbox 'Morgen' ist dann heute faellig)
    sent = tasksrc.digest_run(now + 26 * 3600 + 7200)
    ns = notes()
    ok(f"naechster Tag: neue Mitteilung ({sent})", sent == 1 and len(ns) == 2, ns)
    # Zeitzone: an einer Tagesgrenze zwei Konten mit verschiedenen Zonen
    with connect() as c, c:
        c.execute("DELETE FROM notification WHERE uid=?", (A,))
        c.execute("DELETE FROM user_kv WHERE uid=? AND k='task_due_sent'", (A,))
    # Zeitpunkt: 12:00 UTC am Tag T. Kiritimati (+14) = 02:00 am T+1, Pago Pago (-11) = 01:00 am T
    noon = datetime.combine(today, datetime.min.time(), ZoneInfo("UTC")).replace(hour=12).timestamp()
    tday = today.isoformat()
    with connect() as c, c:
        c.execute("UPDATE task SET due=? WHERE uid=? AND text='Geheimer Inbox-Text'", ((today + timedelta(days=1)).isoformat(), A))
        c.execute("DELETE FROM task WHERE uid=? AND text IN ('Alt', 'Morgen')", (A,))
        c.execute("DELETE FROM note WHERE uid=? AND parent_id=?", (A, db))
    req("PUT", "/api/tasks/digest", body={"time": "00:30", "tz": "Pacific/Pago_Pago"})
    ok(f"Zone -11: am {tday} ist nichts faellig -> keine Mitteilung", tasksrc.digest_run(noon) == 0 and not notes())
    with connect() as c, c:
        c.execute("DELETE FROM user_kv WHERE uid=? AND k='task_due_sent'", (A,))
    req("PUT", "/api/tasks/digest", body={"tz": "Pacific/Kiritimati"})
    sent = tasksrc.digest_run(noon)
    ns = notes()
    ok(f"Zone +14: dort ist schon der Folgetag -> heute faellig ({sent})", sent == 1 and json.loads(ns[0][1]).get("due") == 1
       and json.loads(ns[0][1]).get("day") == (today + timedelta(days=1)).isoformat(), ns)
    # ohne eigene Zone: Zeitzone der Ruhezeiten (Push-Einstellungen) bzw. Server
    with connect() as c, c:
        c.execute("DELETE FROM user_kv WHERE uid=? AND k='task_due_sent'", (A,))
        c.execute("DELETE FROM notification WHERE uid=?", (A,))
    req("PUT", "/api/push/settings", body={"quiet": {"tz": "Pacific/Kiritimati"}})
    req("PUT", "/api/tasks/digest", body={"tz": ""})
    ok("ohne eigene Zone: Zone aus den Push-Einstellungen", tasksrc.digest_run(noon) == 1)
    # Bereich 'tasks' in den Push-Einstellungen abschaltbar
    s, d = req("GET", "/api/push")
    ok(f"Push-Bereich 'tasks' angeboten ({s})", s == 200 and "tasks" in d.get("categories", []), d.get("categories"))
    req("PUT", "/api/push/settings", body={"cats": {"tasks": False}})
    with connect() as c, c:
        c.execute("DELETE FROM user_kv WHERE uid=? AND k='task_due_sent'", (A,))
        c.execute("DELETE FROM push_queue WHERE uid=?", (A,))
    tasksrc.digest_run(noon)
    with connect() as c:
        q = c.execute("SELECT COUNT(*) FROM push_queue WHERE uid=? AND kind='task.due'", (A,)).fetchone()[0]
    ok(f"Bereich aus: Glocke ja, Push nein ({q})", q == 0 and len(notes()) == 2)
    s, d = req("GET", "/api/tasks/digest", B)
    ok("Bert: eigene Einstellung (aus), sieht Annas nicht", s == 200 and d["on"] is False and d["tz"] == "")
    ok("Bert: keine Mitteilungen", not notes(B))


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        us = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'az-%'").fetchall()]
    for u in us:
        delete_user_data(u, drop_user=True)


if __name__ == "__main__":
    main()
    print(f"\nAUFGABEN-ZUSAMMENFASSUNG {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
    sys.exit(0 if res and all(res) else 1)
