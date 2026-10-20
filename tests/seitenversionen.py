"""Versionen beim Bearbeiten und Klartext besonderer Bloecke (2.4, Testrunde W5/W6): Sicherung nach Pause/beim Verlassen
(gedrosselt, keine Doppelten), Wiederherstellen sichert vorher den aktuellen Stand, Markdown-Export/Versionsvorschau/
Suchausschnitt ohne interne Werte (YouTube-Adresse, Kalender-Ansicht, Schaltflaechen-Aktion, Ansichtsnamen).
Wegwerf-Konten sv-*, raeumt auf. Umgebung: HK_TEST_BASE, BASE_URL, SESSION_SECRET, Postgres ueber db.py."""
import base64, json, os, sys, time, urllib.error, urllib.parse, urllib.request
from itsdangerous import TimestampSigner
import auth
from db import connect
from saga import notes as N

B = os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099")
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
res = []


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, ("  -- " + str(info)[:400]) if (info and not c) else "", flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def call(u, m, p, b=None, raw=False):
    h = {"Host": HOST, "Cookie": "saga=" + ck(u), "X-Saga": "1", "Content-Type": "application/json", "Accept-Language": "de"}
    try:
        with urllib.request.urlopen(urllib.request.Request(B + p, data=json.dumps(b).encode() if b is not None else None,
                                                           method=m, headers=h), timeout=20) as r:
            x = r.read()
            return r.status, (x if raw else json.loads(x or b"null"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def upload(u, data, name):
    bd = "sv-test"
    mp = (f"--{bd}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\nContent-Type: text/plain\r\n\r\n").encode() \
        + data + f"\r\n--{bd}--\r\n".encode()
    h = {"Host": HOST, "Cookie": "saga=" + ck(u), "X-Saga": "1", "Content-Type": f"multipart/form-data; boundary={bd}"}
    with urllib.request.urlopen(urllib.request.Request(B + "/api/files", data=mp, method="POST", headers=h), timeout=20) as r:
        return json.loads(r.read())["file"]["url"]


def blocks(*bl):
    return json.dumps({"blocks": list(bl)})


def para(t):
    return {"type": "paragraph", "data": {"text": t}}


def versions(nid):
    with connect() as c:
        return c.execute("SELECT id, body, created FROM note_version WHERE note_id=? ORDER BY created", (nid,)).fetchall()


def age(nid, sec):
    with connect() as c, c:
        c.execute("UPDATE note_version SET created=created-? WHERE note_id=?", (sec, nid))


def cleanup():
    with connect() as c, c:
        c.execute("DELETE FROM note WHERE uid LIKE 'sv-%'")
        c.execute("DELETE FROM file WHERE uid LIKE 'sv-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'sv-%'")


def main():
    cleanup()
    auth.create_user("sv-anna", None, "Anna Version")
    auth.create_user("sv-bert", None, "Bert Version")
    U = "sv-anna"
    nid = call(U, "POST", "/api/notes", {"title": "Urlaub"})[1]["id"]
    rev = call(U, "GET", f"/api/notes/{nid}")[1]["rev"]

    # --- W5: Sicherungen beim Bearbeiten ----------------------------------------------------------------------------
    s, d = call(U, "PATCH", f"/api/notes/{nid}", {"body": blocks(para("Erster Absatz")), "format": "blocks", "rev": rev})
    ok("Erstes Speichern sichert den leeren Anfangsstand", s == 200 and len(versions(nid)) == 1)
    for i in range(5):
        s, d = call(U, "PATCH", f"/api/notes/{nid}", {"body": blocks(para(f"Absatz {i}")), "format": "blocks", "rev": d["rev"]})
    ok("Viele Speicherungen in kurzer Zeit: keine Versionsflut", len(versions(nid)) == 1)
    s, c1 = call(U, "POST", f"/api/notes/{nid}/versions/checkpoint", {"why": "pause"})
    ok("Pause sofort nach der letzten Sicherung: gedrosselt (< 1 min)", s == 200 and c1 == {"created": False} and len(versions(nid)) == 1)
    age(nid, 70)
    s, c1 = call(U, "POST", f"/api/notes/{nid}/versions/checkpoint", {"why": "pause"})
    vs = versions(nid)
    ok("Pause/Verlassen: AKTUELLER Stand wird Version", s == 200 and c1 == {"created": True} and len(vs) == 2 and "Absatz 4" in vs[-1][1])
    age(nid, 70)
    s, c1 = call(U, "POST", f"/api/notes/{nid}/versions/checkpoint", {"why": "leave"})
    ok("Unveraendert: keine zweite gleiche Version", c1 == {"created": False} and len(versions(nid)) == 2)
    age(nid, N.VERSION_EVERY + 5)
    s, d = call(U, "PATCH", f"/api/notes/{nid}", {"body": blocks(para("Nach der Pause")), "format": "blocks", "rev": d["rev"]})
    ok("Speichern nach 5 min: vorheriger Stand schon gesichert -> keine Doppelte", len(versions(nid)) == 2)
    s, d = call(U, "PATCH", f"/api/notes/{nid}", {"body": blocks(para("Weiter geschrieben")), "format": "blocks", "rev": d["rev"]})
    vs = versions(nid)
    ok(f"Laengeres Bearbeiten: {N.VERSION_EVERY // 60} min nach der letzten Sicherung sichert das naechste Speichern",
       len(vs) == 3 and "Nach der Pause" in vs[-1][1], len(vs))
    s, d = call(U, "PATCH", f"/api/notes/{nid}", {"body": blocks(para("Noch weiter")), "format": "blocks", "rev": d["rev"]})
    ok("... danach wieder gedrosselt", len(versions(nid)) == 3)
    ok("Sicherung bei Pause: Rechte wie beim Verlauf (fremd -> 404)", call("sv-bert", "POST", f"/api/notes/{nid}/versions/checkpoint", {})[0] == 404)

    # --- W5: Wiederherstellen sichert vorher den aktuellen Stand (auch kurz nach einer Sicherung) --------------------
    first = call(U, "GET", f"/api/notes/{nid}/versions")[1][-1]["id"]
    n0 = len(versions(nid))
    s, _ = call(U, "POST", f"/api/notes/{nid}/versions/{first}/restore")
    vs = versions(nid)
    ok("Wiederherstellen: aktueller Stand vorher als Version gesichert", s == 200 and len(vs) == n0 + 1 and "Noch weiter" in vs[-1][1])
    cur = call(U, "GET", f"/api/notes/{nid}")[1]
    ok("... und der alte Stand ist zurueck", "Noch weiter" not in json.dumps(cur.get("body", "")))

    # --- W6: Export, Vorschau, Suche ohne interne Werte ---------------------------------------------------------------
    db = call(U, "POST", "/api/notes", {"title": "Reiseziele", "kind": "database"})[1].get("id")
    furl = upload(U, b"Handtuch, Sonnencreme, Buch\n", "packliste.txt")
    body = blocks(
        para("Ostsee Planung"),
        {"type": "embed", "data": {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "kind": "youtube"}},
        {"type": "calendar", "data": {"view": "month", "days": 7}},
        {"type": "buttons", "data": {"buttons": [{"label": "Neu", "icon": "➕", "action": "task", "target": None}]}},
        {"type": "tasks", "data": {}},
        {"type": "database", "data": {"db": db, "view": "v1", "views": [{"id": "v1", "name": "Tabelle", "type": "table"},
                                                                        {"id": "v2", "name": "Kalender", "type": "calendar"}]}},
        {"type": "attaches", "data": {"file": {"url": furl, "name": "packliste.txt", "size": 33, "extension": "txt"}, "title": "packliste.txt"}},
        {"type": "columns", "data": {"cols": [{"blocks": [{"type": "calendar", "data": {"view": "month"}}, para("Spalte Strand")]}]}})
    rev = call(U, "GET", f"/api/notes/{nid}")[1]["rev"]
    s, d = call(U, "PATCH", f"/api/notes/{nid}", {"body": body, "format": "blocks", "rev": rev})
    ok("Seite mit besonderen Bloecken gespeichert", s == 200, d)
    s, md = call(U, "GET", f"/api/notes/{nid}/export", raw=True)
    md = md.decode() if isinstance(md, bytes) else ""
    ok("Markdown: YouTube-Adresse bleibt erhalten", "https://www.youtube.com/watch?v=dQw4w9WgXcQ" in md, md)
    ok("Markdown: keine internen Werte (month, task, youtube als Wort, Ansichtsnamen)",
       "month" not in md and "➕ task" not in md and "Neu ➕ task" not in md and "\nyoutube" not in md and "Tabelle Kalender" not in md, md)
    ok("Markdown: Kalender, Aufgaben und Datenbank als lesbare Hinweise", "Kalender (in Saga)" in md and "Aufgaben-Liste (in Saga)" in md
       and "▦ [" in md, md)
    ok("Markdown: Dateiname nur einmal", md.count("packliste.txt") == 1, md)
    age(nid, N.VERSION_EVERY + 5)
    call(U, "PATCH", f"/api/notes/{nid}", {"body": blocks(para("danach")), "format": "blocks", "rev": d["rev"]})
    vid = call(U, "GET", f"/api/notes/{nid}/versions")[1][0]["id"]
    s, v = call(U, "GET", f"/api/notes/{nid}/versions/{vid}")
    txt = v.get("text", "")
    ok("Versionsvorschau: Titel der Datenbank statt Ansichtsnamen, Platzhalter statt 'month'",
       s == 200 and "▦ Reiseziele" in txt and "month" not in txt and "🗓 Kalender" in txt and "task" not in txt
       and "youtube.com" in txt and txt.count("packliste.txt") == 1 and "Tabelle" not in txt, txt)
    s, d = call(U, "PATCH", f"/api/notes/{nid}", {"body": body, "format": "blocks"})
    s, hits = call(U, "POST", "/api/notes/search", {"q": "ostsee"})
    sn = next((h.get("snippet", "") for h in hits if h.get("id") == nid), "") if isinstance(hits, list) else ""
    ok("Suchausschnitt ohne interne Werte", sn and "month" not in sn and "task" not in sn and "Tabelle" not in sn
       and sn.count("packliste.txt") <= 1, sn)
    txt = N.blocks_text(json.loads(body)["blocks"])
    ok("Suchtext: Schaltflaeche als Beschriftung, Einbettung als Adresse", "➕ Neu" in txt and "youtube.com" in txt and "Spalte Strand" in txt, txt)

    cleanup()
    n = sum(res)
    print(f"\nSEITENVERSIONEN {n}/{len(res)} - {'ALLES GRUEN' if n == len(res) else 'FEHLER'}")
    sys.exit(0 if n == len(res) else 1)


if __name__ == "__main__":
    main()
