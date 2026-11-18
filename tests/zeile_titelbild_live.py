"""Live: Titelbild einer Datenbank-Zeile geaendert -> Betrachter der Datenbank erfahren es (Bug 2122).

Vorher loeste nur props/title/icon announce_row aus - cover/cover_pos nicht: Galerie-Karten (rows[].preview = Titelbild)
anderer Geraete und Personen blieben alt. Geprueft: Eigentuemer A (zweiter Tab mit Anwesenheit auf der Datenbank),
B ueber eine Gruppen-Freigabe "Lesen" auf der Datenbank (Anwesenheit auf der Datenbank) und C ohne Freigabe.
PATCH cover (Galerie-Bild, eigenes hochgeladenes Bild), cover_pos allein, cover null -> je note.changed {id: db, row}
an A-Tab-2 und B (nicht an den ausloesenden Tab, nie an C) und db.rows {db}; /rows liefert danach das neue preview.
Gegenprobe: Inhalt allein (body) meldet KEINE Zeile (unveraendert, Autosave). Wegwerf-Konten zt-*; raeumt auf.
Umgebung wie tests/live.py (HK_TEST_BASE, BASE_URL, SESSION_SECRET; PYTHONPATH=tests/pgshim:.)."""
import io, json, secrets, sys, time
import http.client
import auth
from db import connect
from live import Stream, STREAMS, ck, req, B_, HOST

res = []


def ok(n, c, info=None):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, "" if c or info is None else f"-> {str(info)[:300]}", flush=True)


def upload(cookie, data, name="bild.png"):
    """Eigenes Bild hochladen (POST /api/files, multipart) -> url."""
    bd = "----zt" + secrets.token_hex(8)
    body = io.BytesIO()
    body.write(f"--{bd}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\nContent-Type: image/png\r\n\r\n".encode())
    body.write(data); body.write(f"\r\n--{bd}--\r\n".encode())
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=30)
    c.request("POST", "/api/files", body=body.getvalue(), headers={"Host": HOST, "Cookie": "saga=" + cookie, "X-Saga": "1",
                                                                    "Content-Type": "multipart/form-data; boundary=" + bd})
    r = c.getresponse(); d = json.loads(r.read() or b"{}"); c.close()
    return (d.get("file") or {}).get("url")


def png():
    import struct, zlib
    raw = b"".join(b"\x00" + bytes([200, 40, 40]) * 8 for _ in range(8))
    def chunk(t, x):
        return struct.pack(">I", len(x)) + t + x + struct.pack(">I", zlib.crc32(t + x) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'zt-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        for t in ("note", "note_version", "user_kv", "notification"):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE 'zt-%'")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'zt-%' OR target LIKE 'zt-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'zt-%'")
        c.execute("DELETE FROM groups WHERE name LIKE 'zt-%'")


def main():
    cleanup()
    for u in ("zt-a", "zt-b", "zt-c"):
        auth.create_user(u, None, u)
    with connect() as c, c:
        auth.ensure_group(c, "zt-team")
    for u in ("zt-a", "zt-b"):
        auth.set_user_groups(auth.get_user(u)["id"], ["zt-team"])
    A, Bk, C = ck("zt-a"), ck("zt-b"), ck("zt-c")
    T1, T2 = "zttab" + secrets.token_hex(6), "zttab" + secrets.token_hex(6)
    H1 = {"X-Saga-Tab": T1}
    covers = [x["url"] for x in (req("GET", "/api/covers", A)[1].get("covers") or []) if x.get("builtin")]
    db = req("POST", "/api/notes", A, {"title": "Galerie", "kind": "database"}, hdr=H1)[1]["id"]
    row = req("POST", "/api/notes", A, {"title": "Karte", "parent_id": db}, hdr=H1)[1]["id"]
    s, _ = req("PUT", f"/api/notes/{db}/shares", A, {"type": "group", "target": "zt-team", "role": "view"})
    ok(f"Datenbank an Gruppe geteilt (Lesen) ({s})", s == 200)
    own = upload(A, png())
    ok(f"Eigenes Bild hochgeladen ({own})", bool(own))
    time.sleep(2.3)                                   # gesammelte Ereignisse vom Anlegen abwarten
    s1, s2, sb, sc = Stream(A, tab=T1), Stream(A, tab=T2), Stream(Bk), Stream(C)
    ok("Vier Verbindungen", all(x.wait("hello") is not None for x in (s1, s2, sb, sc)))
    for ckk, h in ((A, {"X-Saga-Tab": T2}), (Bk, None), (C, None)):
        req("POST", "/api/presence", ckk, {"note_id": db, "editing": False}, hdr=h)
    time.sleep(0.5)

    def rowev(st, since):
        return st.wait("note.changed", lambda d: d.get("id") == db and d.get("row") == row, since=since, timeout=4)

    def preview():
        return next((r.get("preview") for r in req("GET", f"/api/notes/{db}/rows", Bk)[1].get("rows") or [] if r["id"] == row), None)

    for name, body, want in (("Galerie-Bild", {"cover": covers[0], "cover_pos": 40}, covers[0]),
                             ("eigenes Bild", {"cover": own}, own),
                             ("nur Position", {"cover_pos": 70}, own),
                             ("anderes Galerie-Bild", {"cover": covers[1]}, covers[1]),
                             ("Titelbild entfernt", {"cover": None}, "")):
        m1, m2, mb, mc = s1.mark(), s2.mark(), sb.mark(), sc.mark()
        st, d = req("PATCH", f"/api/notes/{row}", A, body, hdr=H1)
        ok(f"{name}: PATCH {st}", st == 200, d)
        e2, eb = rowev(s2, m2), rowev(sb, mb)
        ok(f"{name}: zweiter Tab bekommt note.changed mit row", e2 is not None, s2.ev[m2:])
        ok(f"{name}: Gruppen-Betrachter bekommt note.changed mit row", eb is not None, sb.ev[mb:])
        ok(f"{name}: Gruppen-Betrachter bekommt db.rows", sb.wait("db.rows", lambda d: d.get("db") == db, since=mb, timeout=4) is not None)
        time.sleep(0.6)
        ok(f"{name}: ausloesender Tab bekommt es nicht zurueck", s1.find("note.changed", lambda d: d.get("id") == db and d.get("row"), since=m1) is None, s1.ev[m1:])
        ok(f"{name}: Person ohne Freigabe bekommt nichts", not [e for e in sc.ev[mc:] if e[0] in ("note.changed", "db.rows")], sc.ev[mc:])
        ok(f"{name}: /rows zeigt das neue Kartenbild", preview() == want, (preview(), want))
        time.sleep(2.1)                               # db.rows: hoechstens eins je 2 s und Konto

    # Gegenprobe: Inhalt allein meldet keine Zeile an die Datenbank (unveraendert: Autosave soll Tabellen nicht neu laden)
    mb = sb.mark()
    req("PATCH", f"/api/notes/{row}", A, {"format": "blocks", "body": json.dumps({"blocks": [{"id": "z1", "type": "paragraph", "data": {"text": "x"}}]})}, hdr=H1)
    time.sleep(1.5)
    ok("Inhalt allein: kein note.changed mit row an die Datenbank", sb.find("note.changed", lambda d: d.get("id") == db and d.get("row"), since=mb) is None)

    for x in list(STREAMS):
        x.close()
    cleanup()
    bad = res.count(False)
    print(f"{len(res) - bad}/{len(res)} OK" + ("" if bad else " – ALLES GRUEN"))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
