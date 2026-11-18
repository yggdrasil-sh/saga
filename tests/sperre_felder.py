"""Gesperrte Seite: Titelbild, Symbol und Tags -> 423 wie Titel/Inhalt (Bug 2124); Eigenschaften und Entsperren bleiben frei.

Vorher lehnte der Server auf gesperrten Seiten nur title/body ab - Apps und /api/v1 konnten cover, cover_pos, icon und tags
trotzdem aendern (das Web sperrte sie nur per CSS). Geprueft: PATCH /api/notes (Sitzung) je Feld 423 mit
{"error": ...} und unveraendertem Stand; props einer gesperrten Datenbank-Zeile 200; locked:false + icon in einer Anfrage
200; pinned/wide 200; Verschieben, Archivieren, Kommentar 200 (nur dokumentiert, was gilt); /api/v1 PATCH pages/rows mit
icon 423 (code locked); Eigentuemer UND Person mit Freigabe "Bearbeiten". Wegwerf-Konten sf-*; raeumt auf.
Umgebung wie tests/live.py (HK_TEST_BASE, BASE_URL, SESSION_SECRET, DATABASE_URL; PYTHONPATH=tests/pgshim:.)."""
import base64, json, os, sys, time, urllib.error, urllib.parse, urllib.request
from itsdangerous import TimestampSigner
import auth
from db import connect

B = os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8080")
S = TimestampSigner(os.environ["SESSION_SECRET"]); HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
res = []
ERR5 = []                                          # Antworten >= 500 (Serverfehler)


def ok(n, c, info=None):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, "" if c or info is None else f"-> {str(info)[:300]}", flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "sec_at": int(time.time())}).encode())).decode()


def call(m, p, cookie=None, b=None, bearer=None):
    h = {"Host": HOST, "Content-Type": "application/json", "Accept-Language": "de"}
    if cookie:
        h["Cookie"] = "saga=" + cookie; h["X-Saga"] = "1"
    if bearer:
        h["Authorization"] = "Bearer " + bearer
    data = json.dumps(b).encode() if b is not None else None
    try:
        with urllib.request.urlopen(urllib.request.Request(B + p, data=data, method=m, headers=h), timeout=20) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw[:1] in (b"{", b"[") else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        if e.code >= 500:
            ERR5.append((m, p, e.code))
        return e.code, (json.loads(raw) if raw[:1] in (b"{", b"[") else {})


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'sf-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        for t in ("note", "note_version", "user_kv", "notification", "api_token"):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE 'sf-%'")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'sf-%' OR target LIKE 'sf-%'")
        c.execute("DELETE FROM note_comment WHERE owner_uid LIKE 'sf-%' OR uid LIKE 'sf-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'sf-%'")


def main():
    cleanup()
    auth.create_user("sf-a", None, "Sperre A"); auth.create_user("sf-b", None, "Sperre B")
    A, Bk = ck("sf-a"), ck("sf-b")
    covers = [c for c in (call("GET", "/api/covers", A)[1].get("covers") or []) if c.get("builtin")]
    ok(f"Galerie-Bilder vorhanden ({len(covers)})", len(covers) >= 2)
    cv1, cv2 = covers[0]["url"], covers[1]["url"]
    root = call("POST", "/api/notes", A, {"title": "Sperre Felder", "cover": cv1, "icon": "📄"})[1]["id"]
    call("PATCH", f"/api/notes/{root}", A, {"tags": ["alt"]})
    dbid = call("POST", "/api/notes", A, {"title": "DB", "kind": "database", "parent_id": root})[1]["id"]
    call("PATCH", f"/api/notes/{dbid}", A, {"schema": {"props": [{"id": "n", "name": "Notiz", "type": "text"}]}})
    row = call("POST", "/api/notes", A, {"title": "Zeile", "parent_id": dbid})[1]["id"]
    other = call("POST", "/api/notes", A, {"title": "Ziel"})[1]["id"]
    s, _ = call("PUT", f"/api/notes/{root}/shares", A, {"type": "user", "target": "sf-b", "role": "edit"})
    ok(f"Freigabe Bearbeiten an B ({s})", s == 200)
    for nid in (root, row):
        s, _ = call("PATCH", f"/api/notes/{nid}", A, {"locked": True})
        ok(f"Seite {'Zeile' if nid == row else 'Wurzel'} gesperrt ({s})", s == 200)

    def snap(nid):
        n = call("GET", f"/api/notes/{nid}", A)[1]
        return {k: n.get(k) for k in ("title", "icon", "cover", "cover_pos", "tags", "rev", "locked")}

    # --- je Feld 423, nichts geaendert (Eigentuemer und Person mit Bearbeiten) ---------------------------------------
    before = snap(root)
    for who, ckk in (("Eigentuemer", A), ("Bearbeiten", Bk)):
        for body in ({"cover": cv2}, {"cover": None}, {"cover_pos": 10}, {"icon": "🔥"}, {"icon": ""}, {"tags": ["neu"]},
                     {"cover": cv2, "cover_pos": 20}):
            s, d = call("PATCH", f"/api/notes/{root}", ckk, body)
            ok(f"{who}: {json.dumps(body, ensure_ascii=False)} -> 423 mit error", s == 423 and isinstance(d.get("error"), str) and d["error"], (s, d))
    ok("Stand unveraendert (Titelbild, Position, Symbol, Tags, rev)", snap(root) == before, (before, snap(root)))

    # --- erlaubt bleibt -----------------------------------------------------------------------------------------------
    s, d = call("PATCH", f"/api/notes/{row}", Bk, {"props": {"n": "geht"}})
    ok(f"Eigenschaften der gesperrten Zeile (Bearbeiten) -> 200 ({s})", s == 200, d)
    ok("Eigenschaft gespeichert", (call("GET", f"/api/notes/{row}", A)[1].get("props") or {}).get("n") == "geht")
    s, _ = call("PATCH", f"/api/notes/{row}", A, {"icon": "🔥"})
    ok(f"Symbol der gesperrten Zeile -> 423 ({s})", s == 423)
    s, _ = call("PATCH", f"/api/notes/{row}", Bk, {"props": {"n": "mit Symbol"}, "icon": "🔥"})
    ok(f"Eigenschaften + Symbol in einer Anfrage -> 423, nichts geaendert ({s})", s == 423
       and (call("GET", f"/api/notes/{row}", A)[1].get("props") or {}).get("n") == "geht")
    for body in ({"pinned": True}, {"wide": True}):
        s, d = call("PATCH", f"/api/notes/{root}", A, body)
        ok(f"{json.dumps(body)} auf gesperrter Seite -> 200 ({s})", s == 200, d)
    s, d = call("POST", f"/api/notes/{root}/comments", Bk, {"body": "Kommentar trotz Sperre"})
    ok(f"Kommentar auf gesperrter Seite -> 2xx ({s})", s in (200, 201), d)
    s, d = call("POST", f"/api/notes/{root}/move", A, {"parent_id": other, "index": 0})
    ok(f"Verschieben (POST move) gesperrter Seite -> 200 ({s})", s == 200, d)
    call("POST", f"/api/notes/{root}/move", A, {"parent_id": None, "index": 0})
    s, d = call("POST", f"/api/notes/{row}/archive", A, {"archived": True})
    ok(f"Archivieren gesperrter Zeile -> 200 ({s})", s == 200, d)
    call("POST", f"/api/notes/{row}/archive", A, {"archived": False})

    # --- /api/v1 (Token) ---------------------------------------------------------------------------------------------
    auth.save_setting("api", {"enabled": True, "group": "", "max_tokens": 10, "max_days": 365, "unlimited": False})
    time.sleep(5.5)                                   # Einstellungen haelt der Server 5 s zwischengespeichert
    s, d = call("POST", "/api/account/api-tokens", A, {"name": "Sperre", "scopes": ["write:pages", "write:db"]})
    tok = d.get("token") or ""
    ok(f"API-Token ({s})", s in (200, 201) and tok.startswith("sga_"), d)
    s, d = call("PATCH", f"/api/v1/pages/{root}", bearer=tok, b={"icon": "🔥"})
    ok(f"/api/v1 pages icon -> 423 locked ({s})", s == 423 and d.get("code") == "locked", (s, d))
    s, d = call("PATCH", f"/api/v1/pages/{root}", bearer=tok, b={"tags": ["x"]})
    ok(f"/api/v1 pages tags -> 423 ({s})", s == 423, (s, d))
    s, d = call("PATCH", f"/api/v1/rows/{row}", bearer=tok, b={"icon": "🔥"})
    ok(f"/api/v1 rows icon -> 423 ({s})", s == 423, (s, d))
    s, d = call("PATCH", f"/api/v1/rows/{row}", bearer=tok, b={"properties": {"Notiz": "v1"}})
    ok(f"/api/v1 rows Eigenschaften -> 200 ({s})", s == 200, (s, d))
    ok("Stand weiter unveraendert", {k: v for k, v in snap(root).items() if k != "rev"} == {k: v for k, v in before.items() if k != "rev"},
       (before, snap(root)))

    # --- Entsperren (auch zusammen mit anderen Feldern) ----------------------------------------------------------------
    s, d = call("PATCH", f"/api/notes/{root}", A, {"locked": False, "icon": "🔥", "cover": cv2, "cover_pos": 30, "tags": ["neu"]})
    n = snap(root)
    ok(f"locked:false + Symbol/Titelbild/Tags in einer Anfrage -> 200 ({s})", s == 200 and n["icon"] == "🔥" and n["cover"] == cv2
       and n["cover_pos"] == 30 and n["tags"] == ["neu"] and not n["locked"], (s, d, n))
    s, _ = call("PATCH", f"/api/notes/{root}", Bk, {"icon": "📄"})
    ok(f"Entsperrt: Symbol durch Bearbeiten -> 200 ({s})", s == 200)

    with connect() as c, c:
        c.execute("DELETE FROM setting WHERE k='api'")    # wieder Standard
    cleanup()
    bad = res.count(False)
    print(f"{len(res) - bad}/{len(res)} OK" + ("" if bad or ERR5 else " – ALLES GRUEN"), f"| Serverfehler: {len(ERR5)}", ERR5 or "")
    sys.exit(1 if bad or ERR5 else 0)


if __name__ == "__main__":
    main()
