"""Editor-Diagnose (2.5, Tiptap-Umbau T6, saga/pmdiag.py): POST /api/diag.

Geprueft: Feature pmdiag1 in /api/version (Standard an), Rechte (ohne Anmeldung bzw. ohne X-Saga -> 401, Konto mit
Editor.js -> 403, Konto mit Tiptap -> 200), strenge Pruefung (v, sv, mode, Typ, Pfad mit Werten/Leerzeichen/Text,
Art, zu viele Eintraege, leere Liste -> 400; unbekannte Felder verworfen), Groesse (> 8 KB -> 413), Ratenbegrenzung
(1 POST je 55 s je Konto -> 429, anderes Konto nicht betroffen), Log-Zeile "pmdiag" (eine je POST, Konto nur als Hash,
dedupliziert, keine Namen, keine unbekannten Felder), sonst nichts in der Datenbank (Zeilenzahlen aller Tabellen ausser diag_event gleich; Admin -> Diagnose),
Admin-Schalter editor.diag (Admin-Oberflaeche zeigt ihn; aus -> 404, Feature fehlt). Wegwerf-Konten pd-*; raeumt auf.
Lokal: wie tests/anruf_diag.py (Server-Ausgabe nach STATE_DIR/server.log)."""
import base64
import hashlib
import http.client
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import anrufe as A_  # noqa: E402  (Helfer: ck, req, admin, ok)
import auth  # noqa: E402
from db import connect  # noqa: E402
from itsdangerous import TimestampSigner  # noqa: E402
from saga.core import set_user_editor_tiptap  # noqa: E402

ok, req, res = A_.ok, A_.req, A_.res
LOG = os.path.join(os.environ.get("STATE_DIR", "/tmp/hkstage"), "server.log")
USERS = [("pd-a", "Anna Diagnose"), ("pd-b", "Bert Diagnose"), ("pd-c", "Cora Diagnose")]


def body(**kw):
    b = {"v": 1, "mode": "save", "sv": 2, "av": "0123abcdef",
         "items": [{"t": "table", "p": "data.content[2][1]", "k": "fresh"}, {"t": "list", "p": "data.items[0].meta", "k": "norm"},
                   {"t": "table", "p": "data.content[2][1]", "k": "fresh"}]}
    b.update(kw)
    return b


def logtxt():
    try:
        return open(LOG, encoding="utf-8", errors="replace").read()
    except OSError:
        return None


def feats():
    return req("GET", "/api/version")[1].get("features") or []


def counts():
    with connect() as c:
        tabs = [r[0] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()").fetchall()]
        # diag_event (Admin -> Diagnose): dort landet jede Meldung absichtlich - siehe tests/admin_diagnose.py
        return {t: c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tabs if t != "diag_event"}


def cleanup():
    with connect() as c, c:
        c.execute("DELETE FROM user_kv WHERE uid LIKE 'pd-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'pd-%'")


def main():
    cleanup()
    prev = auth.settings(fresh=True)
    keep = prev.get("editor")
    auth.save_setting("editor", {**(prev.get("editor") or {}), "engine": "choice", "diag": True})
    for u, n in USERS:
        auth.get_user(u) or auth.create_user(u, A_.PW, n)
    auth.get_user("pd-adm") or auth.create_user("pd-adm", A_.PW, "Admin Diagnose", is_admin=True)
    set_user_editor_tiptap("pd-a", True)
    set_user_editor_tiptap("pd-b", True)
    set_user_editor_tiptap("pd-c", False)              # M3: ohne Wahl gilt Tiptap - pd-c waehlt ausdruecklich Editor.js
    time.sleep(A_.WAIT)
    try:
        run()
    finally:
        if keep is not None:
            auth.save_setting("editor", keep)
        auth._settings_cache["v"] = None
        cleanup()
    print(f"\nPM-DIAG {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


def run():
    A, B, C = (A_.ck(u) for u, _ in USERS)
    ok(f"/api/version: Feature pmdiag1 (Standard an) ({feats()})", "pmdiag1" in feats())
    ok("Standard in auth.DEFAULTS: editor.diag = True, engine = tiptap (M3)",
       auth.DEFAULTS["editor"].get("diag") is True and auth.DEFAULTS["editor"].get("engine") == "tiptap")

    # --- Rechte -----------------------------------------------------------------------------------------------------
    s, _ = req("POST", "/api/diag", None, body())
    ok(f"ohne Anmeldung: 401 ({s})", s == 401)
    s, _ = req("POST", "/api/diag", A, body(), hdr={"X-Saga": "0"})
    ok(f"ohne X-Saga-Kopf: 401 ({s})", s == 401)
    s, _ = req("POST", "/api/diag", C, body())
    ok(f"Konto mit Editor.js: 403 ({s})", s == 403)

    # --- Pruefung -----------------------------------------------------------------------------------------------------
    it = body()["items"][0]
    bad = [("v=2", body(v=2)), ("v Text", body(v="1")), ("sv fehlt", {k: v for k, v in body().items() if k != "sv"}),
           ("sv Bool", body(sv=True)), ("mode", body(mode="edit")), ("av mit Leerzeichen", body(av="a b")),
           ("items leer", body(items=[])), ("items kein Array", body(items={})), ("21 Eintraege", body(items=[it] * 21)),
           ("Typ gross", body(items=[dict(it, t="Table")])), ("Typ zu lang", body(items=[dict(it, t="a" * 25)])),
           ("Pfad mit Wert", body(items=[dict(it, p="data.text=Hallo Welt")])),
           ("Pfad mit Leerzeichen", body(items=[dict(it, p="data.geheimer name")])),
           ("Pfad mit Anfuehrung", body(items=[dict(it, p='data["x"]')])),
           ("Pfad mit URL", body(items=[dict(it, p="data.file.url/https://example.org/a.png")])),
           ("Pfad zu lang", body(items=[dict(it, p="a." * 81)])), ("Pfad Zahl", body(items=[dict(it, p=3)])),
           ("Art", body(items=[dict(it, k="text")])), ("Eintrag kein Objekt", body(items=["table"]))]
    codes = {n: req("POST", "/api/diag", A, b)[0] for n, b in bad}
    ok(f"falsche Felder -> 400 ({[n for n, c in codes.items() if c != 400]})", all(c == 400 for c in codes.values()))
    s, _ = req("POST", "/api/diag", A, {"raw": "x" * (9 * 1024)})
    ok(f"Body > 8 KB -> 413 ({s})", s == 413)
    n0 = len(logtxt() or "")
    db0 = counts()

    # --- Annehmen, Log-Zeile ----------------------------------------------------------------------------------------
    s, d = req("POST", "/api/diag", A, body(secret="geheim-feld-xyz", items=body()["items"] + [dict(it, name="Anna Diagnose")]))
    ok(f"Tiptap-Konto: 200 ok ({s}, {d})", s == 200 and d.get("ok") is True)
    s2, _ = req("POST", "/api/diag", A, body())
    ok(f"zweiter POST innerhalb 55 s -> 429 ({s2})", s2 == 429)
    s3, _ = req("POST", "/api/diag", B, body(mode="live", items=[{"t": "?", "p": "", "k": "err"}, {"t": "columns", "p": "data.cols[1].*", "k": "pass"}]))
    ok(f"anderes Konto: nicht betroffen (200) ({s3})", s3 == 200)
    s4, _ = req("POST", "/api/diag", A, body(items=[dict(it, p=("data.x" + "[1]" * 25)[:160])]))
    ok(f"auch nach 429 bleibt die Sperre (429) ({s4})", s4 == 429)
    time.sleep(0.3)
    db1 = counts()
    ok(f"nichts in der Datenbank ({[t for t in db1 if db1[t] != db0.get(t)]})", db0 == db1)
    txt = logtxt()
    if txt is None:
        ok(f"Server-Log {LOG} lesbar (Server mit Ausgabe in STATE_DIR/server.log starten)", False)
        return
    lines = [ln for ln in txt[n0:].splitlines() if ln.startswith("pmdiag ")]
    # Admin -> Diagnose (saga/diagstore.py): dieselben Eintraege in diag_event
    acc = [json.loads(ln[len("pmdiag "):]).get("acct") for ln in lines]
    st = []
    for _ in range(20):
        with connect() as c:
            st = [r[0] for r in c.execute("SELECT data FROM diag_event WHERE kind='pmdiag' AND acct IN (?, ?) AND ts > ? ORDER BY id",
                                          (acc + ["-", "-"])[:2] + [time.time() - 120]).fetchall()]
        if len(st) >= 2:
            break
        time.sleep(0.3)
    ok(f"diag_event: beide Meldungen, Inhalt wie die Log-Zeilen ({len(st)})",
       [x if isinstance(x, dict) else json.loads(x) for x in st] == [json.loads(ln[len("pmdiag "):]) for ln in lines])
    ok(f"je angenommenem POST genau eine Zeile 'pmdiag' ({len(lines)})", len(lines) == 2)
    recs = [json.loads(ln[len("pmdiag "):]) for ln in lines]
    a = recs[0] if recs else {}
    ok(f"Zeile: Konto als Hash (10 Hex), keine Namen ({a.get('acct')})",
       re.fullmatch(r"[0-9a-f]{10}", a.get("acct") or "") and not [ln for ln in lines if re.search(r"pd-|Diagnose", ln)])
    ok(f"Zeile: unbekannte Felder verworfen, Eintraege dedupliziert ({a.get('items')})",
       not [ln for ln in lines if "geheim-feld" in ln or '"name"' in ln or '"secret"' in ln]
       and a.get("items") == [{"t": "table", "p": "data.content[2][1]", "k": "fresh"}, {"t": "list", "p": "data.items[0].meta", "k": "norm"}]
       and a.get("n") == 2)
    ok(f"Zeile: Modus, Schema-Version, Asset-Stand ({a.get('mode')}, {a.get('sv')}, {a.get('av')})",
       a.get("mode") == "save" and a.get("sv") == 2 and a.get("av") == "0123abcdef")
    b2 = recs[1] if len(recs) > 1 else {}
    ok("zweites Konto: anderer Hash, mode live, err/pass", b2.get("acct") != a.get("acct") and b2.get("mode") == "live"
       and [x["k"] for x in b2.get("items", [])] == ["err", "pass"])

    # --- Admin-Schalter ---------------------------------------------------------------------------------------------
    s, html = admin("/settings")
    ok(f"Admin-Oberflaeche: Schalter editor_diag, an ({s})", s == 200 and re.search(r'name="editor_diag"[^>]*checked', html or ""))
    auth.save_setting("editor", {**(auth.settings(fresh=True).get("editor") or {}), "diag": False})
    time.sleep(A_.WAIT)
    ok(f"aus: Feature pmdiag1 fehlt ({feats()})", "pmdiag1" not in feats())
    s, _ = req("POST", "/api/diag", B, body())
    ok(f"aus: POST -> 404 ({s})", s == 404)
    auth.save_setting("editor", {**(auth.settings(fresh=True).get("editor") or {}), "diag": True})
    time.sleep(A_.WAIT)
    ok("wieder an: Feature pmdiag1 zurueck", "pmdiag1" in feats())


def admin(path):
    """Admin-Bereich (eigener Port) als pd-adm, wie tests/anrufe.py admin() -> (status, html)."""
    sa = TimestampSigner(hashlib.sha256(("hk-admin|" + A_.SECRET).encode()).hexdigest())
    cookie = sa.sign(base64.b64encode(json.dumps({"aid": "pd-adm", "ep": time.time() + 5, "csrf": "x"}).encode())).decode()
    c = http.client.HTTPConnection(A_.ADM.hostname, A_.ADM.port, timeout=30)
    c.request("GET", path, headers={"Cookie": "hk_admin=" + cookie, "Accept-Language": "de"})
    r = c.getresponse(); data = r.read().decode("utf-8", "replace"); c.close()
    return r.status, data


if __name__ == "__main__":
    main()
