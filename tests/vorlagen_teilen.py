"""2.5: Schaltflaeche "Neuer Eintrag" mit Vorlage auf einer GETEILTEN Seite. Die Vorlage gehoert dem Eigentuemer und
liegt in einer Datenbank, die nicht geteilt ist. Wer die Seite sehen und das Ziel bearbeiten darf, legt per Schaltflaeche
(via = Seite mit genau dieser Schaltflaeche) einen Eintrag aus der Vorlage an - ohne via, mit fremder Vorlage, falschem
Ziel, ohne Bearbeiten-Recht oder ueber eine andere Seite nicht. Wegwerf-Konten vt-*; raeumt auf."""
import base64, http.client, json, os, sys, time, urllib.parse
from itsdangerous import TimestampSigner
import auth
from db import connect

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
res = []


def ok(n, c):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, cookie, body=None):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=30)
    h = {"Host": HOST, "Cookie": "saga=" + cookie, "X-Saga": "1", "Content-Type": "application/json"}
    c.request(m, p, body=json.dumps(body) if body is not None else None, headers=h)
    r = c.getresponse(); d = r.read()
    try:
        d = json.loads(d)
    except ValueError:
        pass
    c.close()
    return r.status, d


def cleanup():
    from saga.userdata import delete_user_data
    for u in ("vt-a", "vt-b"):
        delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM note WHERE uid LIKE 'vt-%'")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'vt-%' OR target LIKE 'vt-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'vt-%'")


def main():
    cleanup()
    prev = auth.settings(fresh=True).get("share_enabled", True)
    auth.save_setting("share_enabled", True)
    auth.create_user("vt-a", "Vorlage-Pass-1234", "Anna Vorlage")
    auth.create_user("vt-b", "Vorlage-Pass-1234", "Bert Knopf")
    A, Bk = ck("vt-a"), ck("vt-b")
    time.sleep(5.6)
    try:
        ziel = req("POST", "/api/notes", A, {"kind": "database", "title": "Bugs"})[1]["id"]
        tdb = req("POST", "/api/notes", A, {"kind": "database", "title": "Vorlagen (privat)"})[1]["id"]
        tpl = req("POST", "/api/notes", A, {"parent_id": tdb, "title": "Bugmeldung", "icon": "🐞"})[1]["id"]
        req("PATCH", f"/api/notes/{tpl}", A, {"template": True, "format": "blocks",
                                               "body": json.dumps({"blocks": [{"type": "paragraph", "data": {"text": "Schritte zum Nachstellen"}}]})})
        andere = req("POST", "/api/notes", A, {"parent_id": tdb, "title": "Geheime Vorlage"})[1]["id"]
        req("PATCH", f"/api/notes/{andere}", A, {"template": True})
        seite = req("POST", "/api/notes", A, {"title": "Fehler melden"})[1]["id"]
        knopf = {"type": "buttons", "data": {"buttons": [{"label": "Neuer Bug", "action": "db", "target": ziel, "tpl": tpl, "title": ""}]}}
        req("PATCH", f"/api/notes/{seite}", A, {"format": "blocks", "body": json.dumps({"blocks": [
            {"type": "columns", "data": {"cols": [{"blocks": [knopf]}]}}, {"type": "database", "data": {"db": ziel}}]})})
        leer = req("POST", "/api/notes", A, {"title": "Ohne Knopf"})[1]["id"]
        for nid in (seite, ziel, leer):
            req("PUT", f"/api/notes/{nid}/shares", A, {"type": "user", "target": "vt-b", "role": "edit"})

        s, _ = req("POST", f"/api/notes/{tpl}/duplicate", Bk, {"parent_id": ziel})
        ok(f"Ohne via (wie bisher): nur der Eigentuemer ({s})", s in (403, 404))
        s, d = req("POST", f"/api/notes/{tpl}/duplicate", Bk, {"parent_id": ziel, "via": seite})
        ok(f"Per Schaltflaeche (via, Knopf in Spalte): angelegt ({s})", s == 200 and d.get("id"))
        if s == 200:
            n = req("GET", f"/api/notes/{d['id']}", A)[1]
            ok("Eintrag gehoert A, liegt im Ziel, Inhalt + Icon aus der Vorlage, keine Vorlage",
               n.get("parent_id") == ziel and "Nachstellen" in n.get("body", "") and n.get("icon") == "🐞" and not n.get("template"))
            with connect() as c:
                r = c.execute("SELECT uid, created_by FROM note WHERE id=?", (d["id"],)).fetchone()
            ok(f"Gezaehlt bei A, angelegt von B ({r})", r and r[0] == "vt-a" and r[1] == "vt-b")
            ok("B sieht den neuen Eintrag in der geteilten Datenbank", d["id"] in [x["id"] for x in req("GET", f"/api/notes/{ziel}/rows", Bk)[1].get("rows", [])])
        s, _ = req("POST", f"/api/notes/{andere}/duplicate", Bk, {"parent_id": ziel, "via": seite})
        ok(f"Andere Vorlage (nicht auf der Schaltflaeche) -> verweigert ({s})", s == 404)
        s, _ = req("POST", f"/api/notes/{tpl}/duplicate", Bk, {"parent_id": ziel, "via": leer})
        ok(f"Seite ohne diese Schaltflaeche -> verweigert ({s})", s == 404)
        s, _ = req("POST", f"/api/notes/{tpl}/duplicate", Bk, {"parent_id": seite, "via": seite})
        ok(f"Anderes Ziel als auf der Schaltflaeche -> verweigert ({s})", s == 404)
        req("PUT", f"/api/notes/{ziel}/shares", A, {"type": "user", "target": "vt-b", "role": "view"})
        s, _ = req("POST", f"/api/notes/{tpl}/duplicate", Bk, {"parent_id": ziel, "via": seite})
        ok(f"Ziel nur lesbar -> verweigert ({s})", s in (403, 404))
        req("PUT", f"/api/notes/{ziel}/shares", A, {"type": "user", "target": "vt-b", "role": "edit"})
        req("DELETE", f"/api/notes/{seite}/shares?type=user&target=vt-b", A)
        s, _ = req("POST", f"/api/notes/{tpl}/duplicate", Bk, {"parent_id": ziel, "via": seite})
        ok(f"Seite nicht (mehr) geteilt -> verweigert ({s})", s in (403, 404))
        s, d = req("POST", f"/api/notes/{tpl}/duplicate", A, {"parent_id": ziel, "via": leer})
        ok(f"Eigentuemer: via stoert nicht ({s})", s == 200)
    finally:
        cleanup()
        auth.save_setting("share_enabled", prev)
    n = sum(res)
    print(f"\nVORLAGEN-TEILEN {n}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
    sys.exit(0 if res and all(res) else 1)


if __name__ == "__main__":
    main()
