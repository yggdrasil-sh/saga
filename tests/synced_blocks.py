"""2.5 P6-6: Synchronisierte Bloecke (saga/synced.py, synced_block, synced_ref). Konten mit Markern ('sy-<16 hex>').
Geprueft:
1. Original anlegen (sid vom Server bzw. vom Client), Kopie auf Seite B: gespeichert nur {sid}, gelesen mit Inhalt und src
   (note, block, role, rev, refs); Original mit src.original + refs; synced_ref/synced_block gepflegt.
2. Rechte/Marker-Leck: Kopie auf einer Seite, die jemand ohne Zugriff auf A sieht -> Platzhalter src.noaccess, weder Titel
   noch Inhalt noch Seiten-ID von A (Marker-Technik, auch Export/Markdown, Kommentar-/Versions-Endpunkte nicht noetig);
   neue Kopie ohne Lesezugriff auf A -> faellt weg; nur Lesen auf A -> role view, Durchschreiben ignoriert ("denied").
3. Durchschreiben: Bearbeiten in B mit sync1 -> Original in A geaendert (rev+1, Version, Live), Antwort synced {sid: ok};
   gleichzeitige Aenderung in A und B an derselben Stelle -> Konfliktblock im Original (Merge Stufe 1), verschiedene
   Bloecke -> beide Aenderungen; Client ohne sync1 (alte App) -> Inhalt ignoriert, Block bleibt.
4. Original geloescht -> Kopien zeigen den letzten Inhalt mit src.gone (nur lesend); Original verschoben (blocks/move) ->
   synced_block.note_id nachgefuehrt, Kopien zeigen weiter den Inhalt; Papierkorb + endgueltig loeschen.
5. Veroeffentlichen: Kopie mit Original desselben Kontos -> Inhalt; Original eines anderen (nicht veroeffentlicht) -> leer.
6. Suche zaehlt den Inhalt nur fuer A; Markdown-Export von B mit Inhalt (Rechte des Exports).
Wegwerf-Konten sy-*, raeumt auf. Umgebung wie die anderen Tests (HK_TEST_BASE, BASE_URL, SESSION_SECRET)."""
import base64
import http.client
import json
import os
import secrets
import sys
import time
import urllib.parse
from itsdangerous import TimestampSigner

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import auth  # noqa: E402
from db import connect  # noqa: E402

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
res, SEEN = [], []
MK = lambda: "sy-" + secrets.token_hex(8)        # noqa: E731
A, B, C = MK(), MK(), MK()                        # Anna (Eigentuemerin), Bert (Bearbeiten auf A und eigene Seite), Cora (nur Seite B)
NAMES = {A: "Anna Sechs", B: "Bert Sechs", C: "Cora Sechs"}
SECRET_TITLE = "Geheimplan-" + secrets.token_hex(4)
SECRET_TEXT = "Tresorcode-" + secrets.token_hex(4)
SYNC = {"X-Saga-Caps": "inline2,cols6,merge1,sync1"}
OLD = {"X-Saga-Caps": "inline2,cols6"}


def ok(n, c, detail=None):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, flush=True)
    if not c and detail is not None:
        print("     ", str(detail)[:1500], flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, u=A, body=None, hdr=None, raw=False):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=60)
    h = {"Host": HOST, "Accept-Language": "de"}
    if u:
        h.update({"X-Saga": "1", "Cookie": "saga=" + ck(u)})
    h.update(hdr or {})
    data = None
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    c.request(m, p, body=data, headers=h)
    r = c.getresponse()
    out = r.read()
    SEEN.append((u, f"{m} {p}", out.decode("utf-8", "replace")))
    if raw:
        return r.status, out.decode("utf-8", "replace")
    try:
        return r.status, json.loads(out) if out[:1] in (b"[", b"{") else out
    except ValueError:
        return r.status, out


def handle(u):
    from saga.core import local_ref
    return local_ref(u)


def P(i, text):
    return {"id": i, "type": "paragraph", "data": {"text": text}}


def page(u, title, blocks):
    s, d = req("POST", "/api/notes", u, {"title": title})
    nid = d["id"]
    s, d = save(nid, blocks, u)
    assert s == 200, (s, d)
    return nid


def save(nid, blocks, u=A, rev=None, hdr=None, merge=False):
    b = {"body": json.dumps({"blocks": blocks}), "format": "blocks"}
    if rev is not None:
        b["rev"] = rev
    if merge:
        b["merge"] = 1
    return req("PATCH", f"/api/notes/{nid}", u, b, hdr=hdr)


def get(nid, u=A):
    s, d = req("GET", f"/api/notes/{nid}", u)
    return d if s == 200 else {}


def blocks(d):
    return json.loads(d.get("body") or '{"blocks": []}')["blocks"]


def stored(nid):
    with connect() as c:
        r = c.execute("SELECT body FROM note WHERE id=?", (nid,)).fetchone()
    return json.loads(r[0])["blocks"] if r else []


def synced_of(bl, sid=None):
    for b in bl:
        if b.get("type") == "synced" and (sid is None or b["data"].get("sid") == sid):
            return b
    return None


def run():
    # --- 1. Original und Kopie ------------------------------------------------------------------------------------------
    na = page(A, SECRET_TITLE, [P("a0", "Vorne"), {"id": "s1", "type": "synced", "data": {"blocks": [P("i1", SECRET_TEXT), P("i2", "Zweiter")]}}])
    orig = synced_of(stored(na))
    sid = (orig or {}).get("data", {}).get("sid", "")
    ok("Original: sid vom Server vergeben (16 Zeichen)", len(sid) == 16 and orig["data"]["blocks"][0]["data"]["text"] == SECRET_TEXT, orig)
    with connect() as c:
        r = c.execute("SELECT owner, note_id, block_id, rev FROM synced_block WHERE sid=?", (sid,)).fetchone()
    ok("synced_block: Eigentuemerin, Seite, Block", r and r[0] == A and r[1] == na and r[2] == "s1", r)
    nb = page(A, "Anna B", [P("b0", "Kopie folgt"), {"id": "k1", "type": "synced", "data": {"sid": sid, "blocks": [P("zz", "egal")]}}])
    st = synced_of(stored(nb))
    ok("Kopie gespeichert nur mit sid (kein Inhalt im body)", st and st["data"] == {"sid": sid}, st)
    d = get(nb)
    k = synced_of(blocks(d))
    ok("Kopie gelesen: Inhalt des Originals + src", k and k["data"]["blocks"][0]["data"]["text"] == SECRET_TEXT
       and k["data"]["src"].get("note") == na and k["data"]["src"].get("block") == "s1" and k["data"]["src"].get("role") == "owner"
       and k["data"]["src"].get("refs") == 1, k)
    o = synced_of(blocks(get(na)))
    ok("Original gelesen: src.original, refs 1", o and o["data"]["src"] == {"original": True, "refs": 1}, o)
    with connect() as c:
        rr = c.execute("SELECT note_id FROM synced_ref WHERE sid=?", (sid,)).fetchall()
    ok("synced_ref: Seite B", [x[0] for x in rr] == [nb], rr)
    time.sleep(3)
    s, d = req("POST", "/api/notes/search", A, {"q": SECRET_TEXT})
    items = d if isinstance(d, list) else (d.get("results") or d.get("notes") or [])
    hits = [x.get("id") for x in items if isinstance(x, dict)]
    ok("Suche: Inhalt nur fuer A (kein Doppeltreffer in B)", na in hits and nb not in hits, d)

    # --- 2. Rechte, Marker ---------------------------------------------------------------------------------------------
    nc = page(A, "Fuer Cora", [{"id": "k2", "type": "synced", "data": {"sid": sid}}, P("c1", "Sichtbar")])
    req("PUT", f"/api/notes/{nc}/shares", A, {"type": "user", "target": handle(C), "role": "edit"})
    d = get(nc, C)
    k = synced_of(blocks(d))
    raw = json.dumps(d, ensure_ascii=False)
    ok("Cora ohne Zugriff auf A: Platzhalter noaccess", k and k["data"] == {"sid": sid, "src": {"noaccess": True}}, k)
    ok("Marker: weder Titel noch Inhalt noch Seiten-ID von A", SECRET_TITLE not in raw and SECRET_TEXT not in raw and f'"note": {na}' not in raw, raw[:500])
    s, md = req("GET", f"/api/notes/{nc}/export", C, raw=True)
    ok("Marker: Markdown-Export fuer Cora ohne Inhalt", s == 200 and SECRET_TEXT not in md and SECRET_TITLE not in md, md[:300])
    # Cora speichert Seite C zurueck, mit Fantasie-Inhalt im Platzhalter -> ignoriert, Original unveraendert
    bl = blocks(d)
    synced_of(bl)["data"]["blocks"] = [P("i1", "von Cora")]
    s, d2 = save(nc, bl, C, rev=d["rev"], hdr=SYNC)
    ok(f"Cora speichert (Platzhalter mit Inhalt) ({s})", s == 200 and (d2.get("synced") or {}).get(sid) == "denied", d2)
    ok("Original unveraendert", synced_of(stored(na))["data"]["blocks"][0]["data"]["text"] == SECRET_TEXT)
    ok("Kopie in C weiter nur sid", synced_of(stored(nc))["data"] == {"sid": sid})
    # Cora legt auf eigener Seite eine neue Kopie an -> faellt weg (kein Lesezugriff auf A)
    ncc = page(C, "Coras", [P("x", "x"), {"id": "k9", "type": "synced", "data": {"sid": sid}}])
    ok("Neue Kopie ohne Lesezugriff auf A: faellt weg", synced_of(stored(ncc)) is None, stored(ncc))
    # Nur Lesen auf A -> role view, Durchschreiben verweigert
    req("PUT", f"/api/notes/{na}/shares", A, {"type": "user", "target": handle(C), "role": "view"})
    d = get(nc, C)
    k = synced_of(blocks(d))
    ok("Cora mit Lesen auf A: Inhalt, role view", k and k["data"]["src"].get("role") == "view" and k["data"]["blocks"][0]["data"]["text"] == SECRET_TEXT, k)
    bl = blocks(d)
    synced_of(bl)["data"]["blocks"][0]["data"]["text"] = "Cora aendert"
    s, d2 = save(nc, bl, C, rev=d["rev"], hdr=SYNC)
    ok(f"Lesen auf A: Durchschreiben verweigert ({s})", s == 200 and (d2.get("synced") or {}).get(sid) == "denied"
       and synced_of(stored(na))["data"]["blocks"][0]["data"]["text"] == SECRET_TEXT, d2)
    req("DELETE", f"/api/notes/{na}/shares", A, {"type": "user", "target": handle(C)})

    # --- 3. Durchschreiben ---------------------------------------------------------------------------------------------
    req("PUT", f"/api/notes/{na}/shares", A, {"type": "user", "target": handle(B), "role": "edit"})
    nbb = page(B, "Berts Seite", [P("q", "Bert"), {"id": "kb", "type": "synced", "data": {"sid": sid}}])
    d = get(nbb, B)
    k = synced_of(blocks(d))
    ok("Bert (Bearbeiten auf A): Kopie mit role edit", k and k["data"]["src"].get("role") == "edit", k)
    rev_a = get(na)["rev"]
    bl = blocks(d)
    synced_of(bl)["data"]["blocks"][1]["data"]["text"] = "Zweiter von Bert"
    s, d2 = save(nbb, bl, B, rev=d["rev"], hdr=SYNC)
    ok(f"Durchschreiben -> ok ({s})", s == 200 and (d2.get("synced") or {}).get(sid) == "ok", d2)
    o = synced_of(stored(na))
    ok("Original in A geaendert", o["data"]["blocks"][1]["data"]["text"] == "Zweiter von Bert", o)
    da = get(na)
    ok("A: rev+1, updated_by Bert", da["rev"] == rev_a + 1 and (da.get("updated_by") or {}).get("name") == "Bert Sechs", (da["rev"], rev_a, da.get("updated_by")))
    ok("Kopie in Berts Seite weiter nur sid", synced_of(stored(nbb))["data"] == {"sid": sid})
    k = synced_of(blocks(get(nb)))
    ok("Andere Kopie (Seite B) zeigt die Aenderung", k["data"]["blocks"][1]["data"]["text"] == "Zweiter von Bert", k)
    with connect() as c:
        wh = c.execute("SELECT 1 FROM synced_ref WHERE sid=? AND note_id=?", (sid, nbb)).fetchone()
    ok("synced_ref: Berts Seite", bool(wh))
    # Alte App (ohne sync1): Inhalt ignoriert
    d = get(nbb, B)
    bl = blocks(d)
    synced_of(bl)["data"]["blocks"][0]["data"]["text"] = "alte App"
    s, d2 = save(nbb, bl, B, rev=d["rev"], hdr=OLD)
    ok(f"Ohne sync1: Inhalt ignoriert ({s})", s == 200 and "synced" not in d2 and synced_of(stored(na))["data"]["blocks"][0]["data"]["text"] == SECRET_TEXT, d2)
    # Gleichzeitig: Bert liest, Anna aendert i1, Bert aendert i1 anders -> Konfliktblock; i2 parallel -> beide
    d = get(nbb, B)
    bl_b = blocks(d)
    da = get(na)
    bl_a = blocks(da)
    synced_of(bl_a)["data"]["blocks"][0]["data"]["text"] = "Anna schreibt alles neu"
    s, _ = save(na, bl_a, A, rev=da["rev"])
    ok(f"Anna aendert das Original ({s})", s == 200)
    synced_of(bl_b)["data"]["blocks"][0]["data"]["text"] = "Bert ganz anders"
    s, d2 = save(nbb, bl_b, B, rev=d["rev"], hdr=SYNC)
    o = synced_of(stored(na))
    texts = [x["data"].get("text") for x in o["data"]["blocks"]]
    conf = [x for x in o["data"]["blocks"] if (x.get("tunes") or {}).get("hkconflict")]
    ok(f"Konflikt: Annas Text bleibt, Berts als Konfliktblock ({s}, {(d2.get('synced') or {}).get(sid)})",
       s == 200 and (d2.get("synced") or {}).get(sid) == "merged" and "Anna schreibt alles neu" in texts and len(conf) == 1
       and conf[0]["data"]["text"] == "Bert ganz anders", (texts, d2))
    d = get(nbb, B)
    bl_b = blocks(d)
    da = get(na)
    bl_a = blocks(da)
    synced_of(bl_a)["data"]["blocks"][-1]["data"]["text"] = "Ende von Anna"
    save(na, bl_a, A, rev=da["rev"])
    synced_of(bl_b)["data"]["blocks"][0]["data"]["text"] = "Anfang von Bert"
    s, d2 = save(nbb, bl_b, B, rev=d["rev"], hdr=SYNC)
    texts = [x["data"].get("text") for x in synced_of(stored(na))["data"]["blocks"]]
    ok("Verschiedene Bloecke gleichzeitig: beide Aenderungen", "Anfang von Bert" in texts and "Ende von Anna" in texts, texts)
    with connect() as c:
        n_v = c.execute("SELECT COUNT(*) FROM note_version WHERE note_id=?", (na,)).fetchone()[0]
    ok("Versionen von A angelegt", n_v >= 1, n_v)
    # Gesperrtes Original
    req("PATCH", f"/api/notes/{na}", A, {"locked": True})
    d = get(nbb, B)
    k = synced_of(blocks(d))
    ok("Original gesperrt: Kopie role view", k["data"]["src"].get("role") == "view", k)
    bl = blocks(d)
    synced_of(bl)["data"]["blocks"][0]["data"]["text"] = "trotz Sperre"
    s, d2 = save(nbb, bl, B, rev=d["rev"], hdr=SYNC)
    ok("Gesperrt: Durchschreiben -> locked", (d2.get("synced") or {}).get(sid) == "locked", d2)
    req("PATCH", f"/api/notes/{na}", A, {"locked": False})

    # --- 7. Webhook page.updated fuer A --------------------------------------------------------------------------------

    # --- 5. Veroeffentlichen -------------------------------------------------------------------------------------------
    req("PUT", "/api/account/public-name", A, {"name": "sy" + secrets.token_hex(3)})
    s, d = req("PUT", f"/api/notes/{nb}/publish", A, {})
    path = urllib.parse.urlparse(d.get("url") or "").path
    s, html = req("GET", path, None, raw=True)
    ok(f"Veroeffentlicht (gleiches Konto): Inhalt der Kopie ({s})", s == 200 and "Anfang von Bert" in html, html[-500:])
    req("DELETE", f"/api/notes/{nb}/publish", A)
    req("PUT", "/api/account/public-name", B, {"name": "sy" + secrets.token_hex(3)})
    s, d = req("PUT", f"/api/notes/{nbb}/publish", B, {})
    path = urllib.parse.urlparse(d.get("url") or "").path
    s, html = req("GET", path, None, raw=True)
    ok(f"Veroeffentlicht (Original eines anderen, nicht oeffentlich): ohne Inhalt ({s})", s == 200 and "Anfang von Bert" not in html
       and SECRET_TITLE not in html and "Bert" in html, html[-500:])
    req("DELETE", f"/api/notes/{nbb}/publish", B)

    # --- 4. Verschieben, Loeschen --------------------------------------------------------------------------------------
    nz = page(A, "Neue Heimat", [P("z0", "Ziel")])
    da = get(na)
    s, d = req("POST", f"/api/notes/{na}/blocks/move", A, {"ids": ["s1"], "to": nz, "rev_a": da["rev"]})
    ok(f"Original verschoben ({s})", s == 200, d)
    with connect() as c:
        r = c.execute("SELECT note_id FROM synced_block WHERE sid=?", (sid,)).fetchone()
    ok("synced_block.note_id nachgefuehrt", r and r[0] == nz, r)
    k = synced_of(blocks(get(nb)))
    ok("Kopie zeigt weiter den Inhalt, src.note = neue Seite", k["data"]["src"].get("note") == nz and "Anfang von Bert" in json.dumps(k), k)
    k = synced_of(blocks(get(nbb, B)))
    ok("Bert (kein Zugriff auf die neue Heimat): Platzhalter", k["data"]["src"] == {"noaccess": True}, k)
    dz = get(nz)
    bl = [x for x in blocks(dz) if x.get("type") != "synced"]
    s, _ = save(nz, bl, A, rev=dz["rev"])
    with connect() as c:
        r = c.execute("SELECT gone_at FROM synced_block WHERE sid=?", (sid,)).fetchone()
    ok(f"Original aus der Seite geloescht -> gone_at ({s})", r and r[0] is not None, r)
    k = synced_of(blocks(get(nb)))
    ok("Kopie: letzter Inhalt mit src.gone, nur lesend", k["data"]["src"].get("gone") is True and k["data"]["src"].get("role") == "view"
       and "Anfang von Bert" in json.dumps(k), k)
    # Wiederherstellen: Original wieder einfuegen (gleiche sid) -> wieder aktiv
    dz = get(nz)
    s, _ = save(nz, blocks(dz) + [{"id": "s1", "type": "synced", "data": {"sid": sid, "blocks": [P("i1", "zurueck")]}}], A, rev=dz["rev"])
    with connect() as c:
        r = c.execute("SELECT gone_at, note_id FROM synced_block WHERE sid=?", (sid,)).fetchone()
    ok("Original wieder eingefuegt -> aktiv", r and r[0] is None and r[1] == nz, r)
    req("DELETE", f"/api/notes/{nz}", A)
    with connect() as c, c:
        from saga.notes import _purge_ids
        _purge_ids(c, A, [nz])
        r = c.execute("SELECT gone_at FROM synced_block WHERE sid=?", (sid,)).fetchone()
    ok("Seite endgueltig geloescht -> Original gone", r and r[0] is not None, r)
    k = synced_of(blocks(get(nb)))
    ok("Kopie nach endgueltigem Loeschen: letzter Inhalt, gone", k["data"]["src"].get("gone") is True and "zurueck" in json.dumps(k), k)

    # --- Verschachtelt / doppelt ---------------------------------------------------------------------------------------
    nn = page(A, "Verschachtelt", [{"id": "o1", "type": "synced", "data": {"blocks": [P("n1", "aussen"),
                                    {"id": "o2", "type": "synced", "data": {"blocks": [P("n2", "innen")]}}]}}])
    o = synced_of(stored(nn))
    ok("synced in synced: Inhalt rueckt nach oben", [x["type"] for x in o["data"]["blocks"]] == ["paragraph", "paragraph"], o)
    sid2 = o["data"]["sid"]
    nd = page(A, "Doppelt", [{"id": "d1", "type": "synced", "data": {"sid": sid2}}, {"id": "d2", "type": "synced", "data": {"sid": sid2}}])
    ok("Gleiche sid zweimal: nur eine Kopie", len([x for x in stored(nd) if x["type"] == "synced"]) == 1, stored(nd))


def scan():
    hits = []
    for who, what, txt in SEEN:
        for u in (A, B, C):
            if u != who and u in txt:
                hits.append((who, what, u))
        if who == C and (SECRET_TITLE in txt) and "/publish" not in what:
            hits.append((who, what, "Titel von A"))
    ok(f"Marker-Scan: {len(SEEN)} Antworten ohne Anmeldenamen anderer, Cora nie Titel von A", not hits, hits[:3])


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        us = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'sy-%'").fetchall()]
    for u in us:
        delete_user_data(u, drop_user=True)
    with connect() as c, c:
        c.execute("DELETE FROM note WHERE uid LIKE 'sy-%'")
        c.execute("DELETE FROM synced_block WHERE owner LIKE 'sy-%'")


def main():
    from saga.i18n import set_lang
    set_lang("de")
    cleanup()
    keep = auth.settings(fresh=True).get("share_enabled")
    auth.save_setting("share_enabled", True)
    for u in (A, B, C):
        auth.create_user(u, "Sync-Pass-1234", NAMES[u])
    time.sleep(5.6)
    try:
        run()
    except Exception:
        import traceback
        traceback.print_exc()
        res.append(False)
    finally:
        scan()
        cleanup()
        auth.save_setting("share_enabled", keep)


if __name__ == "__main__":
    main()
    print(f"\nSYNCED {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
    sys.exit(0 if res and all(res) else 1)
