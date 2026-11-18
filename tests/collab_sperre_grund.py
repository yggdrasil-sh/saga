"""WS /api/collab: reject readonly nennt den Grund (why) - Grundlage fuer "vormerken statt verwerfen" (Bug 2123).

Sperre ohne Ereignis (direkt in der Datenbank gesetzt, wie ein verpasstes hello): erstes Update -> reject readonly
why "locked", weitere Updates (Abo jetzt nur lesen) ebenso "locked". Entsperren per PATCH -> hello update ro false,
danach angenommen. Rolle auf "Lesen" gesenkt ohne Ereignis -> why "role". Abo mit Rolle "Lesen" -> why "role".
Gegenprobe Server 4cd1f44: why fehlt (7 FEHLER); aeltere Clients pruefen nur code und bleiben unberuehrt.
Wegwerf-Konten cg-*; schaltet notes.live fuer die Dauer an und stellt es zurueck.
Umgebung wie tests/collab.py (HK_TEST_BASE, BASE_URL, SESSION_SECRET, DATABASE_URL; PYTHONPATH=tests/pgshim:.)."""
import base64, http.client, json, os, sys, time, urllib.parse
from itsdangerous import TimestampSigner
from pycrdt import XmlFragment, XmlText
import auth
from db import connect
from collab_client import Client

B = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
WS = f"ws://{B.hostname}:{B.port}/api/collab"
S = TimestampSigner(os.environ["SESSION_SECRET"])
A, BB = "cg-a", "cg-b"
res = []


def ok(n, c, info=None):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, "" if c or info is None else f"-> {str(info)[:300]}", flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, u=A, body=None):
    c = http.client.HTTPConnection(B.hostname, B.port, timeout=30)
    c.request(m, p, body=json.dumps(body) if body is not None else None,
              headers={"Cookie": "saga=" + ck(u), "X-Saga": "1", "Content-Type": "application/json"})
    r = c.getresponse(); d = r.read(); c.close()
    try:
        d = json.loads(d)
    except ValueError:
        pass
    return r.status, d


def client(u):
    return Client(WS, {"Cookie": "saga=" + ck(u)})


def subscribe(c, nid):
    c.sub(nid)
    h = c.wait("HELLO", nid)
    c.wait("SYNC2", nid)
    return json.loads(h[2][0]) if h and h[0] == "HELLO" else None


def text_of(d, i):
    el = list(d.get("prosemirror", type=XmlFragment).children)[i]
    return next(x for x in el.children if isinstance(x, XmlText))


def reject(c, nid, ref):
    c.edit(nid, lambda d: text_of(d, 0).insert(0, "X"), ref)
    m = c.wait(("ACK", "REJECT"), nid)
    return (m[0], json.loads(m[2][0]) if m and m[0] == "REJECT" else None) if m and m[0] != "CLOSED" else (None, None)


def sql(q, *a):
    with connect() as c, c:
        c.execute(q, a)


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'cg-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        for t in ("note", "note_version", "user_kv", "notification"):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE 'cg-%'")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'cg-%' OR target LIKE 'cg-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'cg-%'")


def main():
    cleanup()
    prev = bool((auth.settings(fresh=True).get("notes") or {}).get("live"))
    if not prev:
        auth.save_setting("notes", {"live": True}); time.sleep(5.2)
    auth.create_user(A, None, A); auth.create_user(BB, None, BB)
    st, d = req("POST", "/api/notes", A, {"title": "Grund"})
    nid = d["id"]
    req("PATCH", f"/api/notes/{nid}", A, {"format": "blocks", "body": json.dumps({"blocks": [{"id": "g1", "type": "paragraph", "data": {"text": "Start"}}]})})
    req("PUT", f"/api/notes/{nid}/shares", A, {"type": "user", "target": BB, "role": "edit"})
    a, b = client(A), client(BB)
    ha, hb = subscribe(a, nid), subscribe(b, nid)
    ok("Abos: beide schreibberechtigt", ha and not ha["ro"] and hb and not hb["ro"], (ha, hb))
    k, r = reject(a, nid, "a0")
    ok(f"Ausgang: Update angenommen ({k})", k == "ACK", r)

    # --- Sperre ohne Ereignis -----------------------------------------------------------------------------------------
    sql("UPDATE note SET locked=1 WHERE id=?", nid)
    k, r = reject(a, nid, "a1")
    ok("Gesperrt (verpasst): reject readonly mit why locked", k == "REJECT" and r.get("code") == "readonly" and r.get("why") == "locked", (k, r))
    ok("... ref kommt zurueck", r and r.get("ref") == "a1", r)
    k, r = reject(a, nid, "a2")
    ok("Weiteres Update (Abo jetzt nur lesen): why locked", k == "REJECT" and r.get("why") == "locked", (k, r))
    k, r = reject(b, nid, "b1")
    ok("Bearbeiten-Freigabe, gesperrt: why locked", k == "REJECT" and r.get("why") == "locked", (k, r))
    st, _ = req("PATCH", f"/api/notes/{nid}", A, {"locked": False})
    m = a.wait("HELLO", nid, timeout=8)
    ok(f"Entsperrt ({st}) -> hello update ro false", m and not json.loads(m[2][0])["ro"], m and m[2][0])
    k, r = reject(a, nid, "a3")
    ok(f"Danach angenommen ({k})", k == "ACK", r)

    # --- Rolle ohne Ereignis gesenkt ----------------------------------------------------------------------------------
    b.wait("HELLO", nid, timeout=3)
    sql("UPDATE note_share SET role='view' WHERE owner_uid=? AND target=?", A, BB)
    k, r = reject(b, nid, "b2")
    ok("Rolle Lesen (verpasst): why role", k == "REJECT" and r.get("code") == "readonly" and r.get("why") == "role", (k, r))
    k, r = reject(b, nid, "b3")
    ok("Weiteres Update: why role", k == "REJECT" and r.get("why") == "role", (k, r))
    b.close()
    v = client(BB)
    hv = subscribe(v, nid)
    ok("Neues Abo mit Lesen: hello ro", hv and hv["ro"] and hv["role"] == "view", hv)
    k, r = reject(v, nid, "v1")
    ok("Abo Lesen: why role", k == "REJECT" and r.get("why") == "role", (k, r))
    # Lesen + gesperrt: Grund bleibt die Rolle (Entsperren hilft nicht)
    sql("UPDATE note SET locked=1 WHERE id=?", nid)
    k, r = reject(v, nid, "v2")
    ok("Lesen und gesperrt: why role", k == "REJECT" and r.get("why") == "role", (k, r))
    sql("UPDATE note SET locked=0 WHERE id=?", nid)

    for c in (a, v):
        c.close()
    if not prev:
        auth.save_setting("notes", {"live": False})
    cleanup()
    bad = res.count(False)
    print(f"{len(res) - bad}/{len(res)} OK" + ("" if bad else " – ALLES GRUEN"))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
