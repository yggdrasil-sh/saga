"""Seiten-Editor-Schalter (2.5, Tiptap-Umbau T2): Admin editor.engine, Konto-Wahl GET/PUT /api/account/editor,
<body data-editor> auf /notes, Feature pm1 in /api/version. Seit M3 (3.11.2026) Standard tiptap; bei choice gilt ohne
eigene Wahl Tiptap, ausdruecklich Editor.js = user_kv '0'. Laeuft gegen den Testserver (Einstellungen direkt in der DB)."""
import base64, json, os, re, sys, time, urllib.request, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from itsdangerous import TimestampSigner
import auth
from saga import core
B = os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8080")
S = TimestampSigner(os.environ["SESSION_SECRET"]); ck = lambda u: S.sign(base64.b64encode(json.dumps({"uid": u}).encode())).decode()
A = os.environ.get("CK") or ck("hk-test-a")                        # CK: Sitzungs-Cookie von hk-test-a
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc


def call(m, p, b=None, raw=False):
    h = {"Host": HOST, "Content-Type": "application/json", "Cookie": "saga=" + A, "X-Saga": "1"}
    try:
        with urllib.request.urlopen(urllib.request.Request(B + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers=h), timeout=20) as r:
            body = r.read()
            return r.status, (body.decode() if raw else json.loads(body))
    except urllib.error.HTTPError as e:
        return e.code, {}


res = []
def ok(n, c, info=None): res.append(bool(c)); print(("OK  " if c else "FAIL"), n, "" if c else info)
def admin(v):
    auth.save_setting("editor", {"engine": v}); time.sleep(5.5)          # Server haelt Einstellungen 5 s vor
def kv():
    from db import connect
    with connect() as c:
        r = c.execute("SELECT v FROM user_kv WHERE uid=? AND k='editor_tiptap'", ("hk-test-a",)).fetchone()
    return r[0] if r else None
def kv_set(v):
    from db import connect
    with connect() as c, c:
        if v is None:
            c.execute("DELETE FROM user_kv WHERE uid=? AND k='editor_tiptap'", ("hk-test-a",))
        else:
            c.execute("INSERT OR REPLACE INTO user_kv (uid, k, v) VALUES (?, 'editor_tiptap', ?)", ("hk-test-a", v))
def body_editor():
    st, html = call("GET", "/notes", raw=True)
    m = re.search(r'<body[^>]*data-editor="([a-z]+)"', html or "")
    return m.group(1) if m else None


def _saved():
    from db import connect
    with connect() as c:
        r = c.execute("SELECT v FROM setting WHERE k='editor'").fetchone()
    return json.loads(r[0]) if r else None
was_saved = _saved()                                             # gespeicherter Wert (nicht der mit Standard gemischte)
mine = kv()                                                     # roher Wert: None / "0" / "1"
try:
    st, v = call("GET", "/api/version")
    ok("/api/version meldet pm1", "pm1" in (v.get("features") or []), v.get("features"))
    ok("auth.DEFAULTS: engine = tiptap (M3)", auth.DEFAULTS["editor"].get("engine") == "tiptap", auth.DEFAULTS["editor"])
    from db import connect
    with connect() as c, c:
        c.execute("DELETE FROM setting WHERE k='editor'")            # nie gespeichert -> Standard
    auth.settings(fresh=True); time.sleep(5.5)
    kv_set(None)
    st, d = call("GET", "/api/account/editor")
    ok("Ohne Setting: Standard tiptap fuer alle", st == 200 and d.get("admin") == "tiptap" and d.get("engine") == "tiptap" and not d.get("choice"), d)
    ok("Ohne Setting: /notes data-editor=tiptap", body_editor() == "tiptap", body_editor())
    admin("editorjs")
    st, d = call("GET", "/api/account/editor")
    ok("Ausdruecklich editorjs bleibt editorjs, keine Wahl", st == 200 and d == {"admin": "editorjs", "choice": False, "tiptap": True, "engine": "editorjs"}, d)
    ok("PUT ohne Freigabe: 403", call("PUT", "/api/account/editor", {"tiptap": True})[0] == 403)
    ok("/notes: data-editor=editorjs", body_editor() == "editorjs", body_editor())
    admin("choice")
    st, d = call("GET", "/api/account/editor")
    ok("choice ohne eigene Wahl: Tiptap", d.get("choice") is True and d.get("tiptap") is True and d.get("engine") == "tiptap" and body_editor() == "tiptap", d)
    kv_set("1")
    ok("choice: altes '1' bleibt Tiptap", call("GET", "/api/account/editor")[1].get("engine") == "tiptap")
    ok("PUT ungueltig: 400", call("PUT", "/api/account/editor", {"tiptap": "ja"})[0] == 400)
    st, d = call("PUT", "/api/account/editor", {"tiptap": True})
    ok("choice: Konto waehlt Tiptap", st == 200 and d.get("engine") == "tiptap" and core.user_editor_tiptap("hk-test-a"), d)
    ok("/notes: data-editor=tiptap", body_editor() == "tiptap", body_editor())
    st, d = call("PUT", "/api/account/editor", {"tiptap": False})
    ok("choice: Konto waehlt ausdruecklich Editor.js (gespeichert als '0')", d.get("engine") == "editorjs" and body_editor() == "editorjs" and kv() == "0", (d, kv()))
    st, d = call("GET", "/api/account/editor")
    ok("choice: Editor.js-Wahl bleibt nach erneutem Laden", d.get("tiptap") is False and d.get("engine") == "editorjs", d)
    admin("tiptap")
    st, d = call("GET", "/api/account/editor")
    ok("Admin tiptap gilt fuer alle", d.get("engine") == "tiptap" and body_editor() == "tiptap", d)
    auth.save_setting("editor", {"engine": "quatsch"}); time.sleep(5.5)
    ok("Unbekannter Wert -> tiptap", call("GET", "/api/account/editor")[1].get("engine") == "tiptap")
finally:
    if was_saved:
        auth.save_setting("editor", was_saved)
    else:
        from db import connect
        with connect() as c, c:
            c.execute("DELETE FROM setting WHERE k='editor'")
    kv_set(mine)
n = sum(res)
print(f"\nEDITOR-SCHALTER {n}/{len(res)} - {'ALLES GRUEN' if n == len(res) else 'FEHLER'}")
sys.exit(0 if n == len(res) else 1)
