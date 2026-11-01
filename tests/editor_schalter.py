"""Seiten-Editor-Schalter (2.5, Tiptap-Umbau T2): Admin editor.engine, Konto-Wahl GET/PUT /api/account/editor,
<body data-editor> auf /notes, Feature pm1 in /api/version. Laeuft gegen den Testserver (Einstellungen direkt in der DB)."""
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
def body_editor():
    st, html = call("GET", "/notes", raw=True)
    m = re.search(r'<body[^>]*data-editor="([a-z]+)"', html or "")
    return m.group(1) if m else None


was = auth.settings(fresh=True).get("editor")
mine = core.user_editor_tiptap("hk-test-a")
try:
    st, v = call("GET", "/api/version")
    ok("/api/version meldet pm1", "pm1" in (v.get("features") or []), v.get("features"))
    admin("editorjs")
    st, d = call("GET", "/api/account/editor")
    ok("Standard editorjs: keine Wahl", st == 200 and d == {"admin": "editorjs", "choice": False, "tiptap": mine, "engine": "editorjs"}, d)
    ok("PUT ohne Freigabe: 403", call("PUT", "/api/account/editor", {"tiptap": True})[0] == 403)
    ok("/notes: data-editor=editorjs", body_editor() == "editorjs", body_editor())
    admin("choice")
    ok("PUT ungueltig: 400", call("PUT", "/api/account/editor", {"tiptap": "ja"})[0] == 400)
    st, d = call("PUT", "/api/account/editor", {"tiptap": True})
    ok("choice: Konto waehlt Tiptap", st == 200 and d.get("engine") == "tiptap" and core.user_editor_tiptap("hk-test-a"), d)
    ok("/notes: data-editor=tiptap", body_editor() == "tiptap", body_editor())
    st, d = call("PUT", "/api/account/editor", {"tiptap": False})
    ok("choice: Konto zurueck auf Editor.js", d.get("engine") == "editorjs" and body_editor() == "editorjs", d)
    admin("tiptap")
    st, d = call("GET", "/api/account/editor")
    ok("Admin tiptap gilt fuer alle", d.get("engine") == "tiptap" and body_editor() == "tiptap", d)
    auth.save_setting("editor", {"engine": "quatsch"}); time.sleep(5.5)
    ok("Unbekannter Wert -> editorjs", call("GET", "/api/account/editor")[1].get("engine") == "editorjs")
finally:
    auth.save_setting("editor", was or {"engine": "editorjs"})
    core.set_user_editor_tiptap("hk-test-a", mine)
n = sum(res)
print(f"\nEDITOR-SCHALTER {n}/{len(res)} - {'ALLES GRUEN' if n == len(res) else 'FEHLER'}")
sys.exit(0 if n == len(res) else 1)
