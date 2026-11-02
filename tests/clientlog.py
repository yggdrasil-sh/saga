"""Fehlerberichte und "Diagnose senden" (2.5, saga/clientlog.py): POST /api/client/log und /api/client/diag.

Geprueft: Feature clientlog1 in /api/version (Standard an), Rechte (ohne Anmeldung bzw. ohne X-Saga -> 401), strenge
Pruefung (falsche Typen/Werte/Anzahlen -> 400, unbekannte Felder verworfen), Groesse (> 16 KB bzw. > 64 KB -> 413),
Bereinigung (Probe-Inhalte - Seitentitel, E-Mail, URL mit Pfad/Query, UUID, Hex, Schluessel, Handle, Zahlen, fremde
Skript-Pfade - stehen NICHT in der Log-Zeile; Stack auf static/<datei>.js:Zeile:Spalte gekuerzt; Endpunkt-Muster
/api/notes/:id/...), Log-Zeilen "clientlog"/"clientdiag" (eine je POST, Konto nur als Hash, kein Konto-/Anzeigename,
keine IP-Adresse; Diagnose-Kennung D-XXXXX in Antwort und Zeile; Freitext nur dort, als user_note, Geheimnisse maskiert),
Ratenbegrenzung (10/min je Konto, 5 Diagnosen/h je Konto, IP-Grenze), nichts in der Datenbank (Zeilenzahlen aller
Tabellen unveraendert), Admin-Schalter diag.clientlog ueber das echte Formular (aus -> 404 + kein Feature, wieder an).
Wegwerf-Konten cl-<zufall>-*; raeumt auf.
Aufruf: PYTHONPATH=tests/pgshim:. python tests/clientlog.py (Server mit Ausgabe in $STATE_DIR/server.log)."""
import base64
import hashlib
import html.parser
import http.client
import json
import os
import re
import secrets
import sys
import time
import urllib.parse

from itsdangerous import TimestampSigner

import auth
from db import connect

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
ADM = urllib.parse.urlparse(os.environ.get("HK_TEST_ADMIN", "http://127.0.0.1:8098"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
SECRET = os.environ["SESSION_SECRET"]
LOG = os.path.join(os.environ.get("STATE_DIR", "/tmp/hkstage"), "server.log")
WAIT = 5.6
RUN = secrets.token_hex(3)
PFX = f"cl-{RUN}-"
res = []

# Probe-Inhalte: duerfen nie in einer Log-Zeile auftauchen
TITLE = "Geheimer Reiseplan Kuchenbude"
MAIL = "anna.probe@example.org"
URLP = "https://cloud.example.org/s/geheimordner?token=abc123"
UUID = "550e8400-e29b-41d4-a716-446655440000"
HEXP = "deadbeefcafe12345678"
KEY = "hkd_Zx9QwErTyUiOpAsDfGhJkL"
HANDLE = "~6v57cuabtwfufsdz"
NUMP = "4711815"
PROBES = [TITLE, "Kuchenbude", "Reiseplan", MAIL, "example.org", "geheimordner", "abc123", UUID, HEXP, KEY, "Zx9QwErTy",
          HANDLE, NUMP, "evil-cdn"]


def ok(n, c):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, flush=True)


def ck(u):
    info = auth.user_info(u, fresh=True)
    return TimestampSigner(SECRET).sign(base64.b64encode(json.dumps({"uid": u, "name": info["name"], "pwc": info["epoch"] or 0}).encode())).decode()


def req(m, p, cookie=None, body=None, hdr=None, raw=None):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=30)
    h = {"Host": HOST, "Accept-Language": "de"}
    data = raw
    if cookie:
        h["Cookie"] = "saga=" + cookie
        h["X-Saga"] = "1"
    if body is not None:
        data = json.dumps(body)
    if data is not None:
        h["Content-Type"] = "application/json"
    h.update(hdr or {})
    c.request(m, p, body=data, headers=h)
    r = c.getresponse()
    out = r.read()
    try:
        out = json.loads(out)
    except ValueError:
        out = out.decode("utf-8", "replace")
    c.close()
    return r.status, out


def adm_cookie(aid):
    sa = TimestampSigner(hashlib.sha256(("hk-admin|" + SECRET).encode()).hexdigest())
    return sa.sign(base64.b64encode(json.dumps({"aid": aid, "ep": time.time() + 5, "csrf": "x"}).encode())).decode()


def admin(m, path, aid, form=None):
    c = http.client.HTTPConnection(ADM.hostname, ADM.port, timeout=30)
    h = {"Cookie": "hk_admin=" + adm_cookie(aid), "Accept-Language": "de"}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form)
        h["Content-Type"] = "application/x-www-form-urlencoded"
    c.request(m, path, body=data, headers=h)
    r = c.getresponse()
    out = r.read().decode("utf-8", "replace")
    c.close()
    return r.status, out


class FormParser(html.parser.HTMLParser):
    """Felder des ersten <form method=post> (so wie ein Browser sie absenden wuerde)."""

    def __init__(self):
        super().__init__()
        self.fields, self.inform, self.sel, self.sel_first, self.ta = [], False, None, None, None

    def handle_starttag(self, tag, a):
        a = dict(a)
        if tag == "form" and (a.get("method") or "").lower() == "post" and not self.fields:
            self.inform = True
        if not self.inform:
            return
        if tag == "input" and a.get("name"):
            ty = (a.get("type") or "text").lower()
            if ty in ("checkbox", "radio"):
                if "checked" in a:
                    self.fields.append((a["name"], a.get("value") or "on"))
            elif ty not in ("submit", "button", "file"):
                self.fields.append((a["name"], a.get("value") or ""))
        elif tag == "select":
            self.sel, self.sel_first = a.get("name"), None
        elif tag == "option" and self.sel:
            v = a.get("value") or ""
            if self.sel_first is None:
                self.sel_first = v
            if "selected" in a:
                self.fields.append((self.sel, v))
                self.sel = None
        elif tag == "textarea" and a.get("name"):
            self.ta = [a["name"], ""]

    def handle_data(self, d):
        if self.ta:
            self.ta[1] += d

    def handle_endtag(self, tag):
        if tag == "select" and self.sel:
            self.fields.append((self.sel, self.sel_first or ""))
            self.sel = None
        elif tag == "textarea" and self.ta:
            self.fields.append(tuple(self.ta))
            self.ta = None
        elif tag == "form":
            self.inform = False


def logtxt():
    try:
        return open(LOG, encoding="utf-8", errors="replace").read()
    except OSError:
        return None


def lines_since(n0, prefix):
    time.sleep(0.3)
    return [ln for ln in (logtxt() or "")[n0:].splitlines() if ln.startswith(prefix + " ")]


def feats():
    return req("GET", "/api/version")[1].get("features") or []


def counts():
    with connect() as c:
        tabs = [r[0] for r in c.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
                                        " AND table_type = 'BASE TABLE'").fetchall()]
        return {tb: c.execute(f'SELECT count(*) FROM "{tb}"').fetchone()[0] for tb in sorted(tabs)}


def head(**kw):
    b = {"v": 1, "app": "web", "ver": "2.5.0-rc.9", "os": "Linux; Chrome 130", "dev": "desktop", "sid": "s" + secrets.token_hex(8)}
    b.update(kw)
    return b


def item(**kw):
    x = {"k": "error", "msg": "TypeError: x is undefined", "src": "http://h/static/pages.js?v=1", "line": 12, "col": 5, "screen": "pages",
         "t": 1234}
    x.update(kw)
    return x


def cleanup():
    with connect() as c, c:
        for u in [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'cl-%'").fetchall()]:
            c.execute("DELETE FROM user_kv WHERE uid=?", (u,))
            c.execute("DELETE FROM users WHERE username=?", (u,))


def main():
    cleanup()
    prev = auth.settings(fresh=True).get("diag")
    auth.save_setting("diag", {"clientlog": True})
    users = {k: PFX + k for k in ("a", "b", "c", "d")}
    for k, u in users.items():
        auth.create_user(u, None, {"a": "Anna Probe", "b": "Bert Probe", "c": "Cora Probe", "d": "Dora Probe"}[k])
    auth.create_user(PFX + "adm", None, "Admin Probe", is_admin=True)
    time.sleep(WAIT)
    try:
        run(users)
    finally:
        auth.save_setting("diag", prev if isinstance(prev, dict) else {"clientlog": True})
        auth._settings_cache["v"] = None
        cleanup()
    print(f"\nCLIENTLOG {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


def run(users):
    A, B, Cc, D = (ck(users[k]) for k in ("a", "b", "c", "d"))
    ok("/api/version: Feature clientlog1 (Standard an)", "clientlog1" in feats())
    ok("Standard in auth.DEFAULTS: diag.clientlog = True", auth.DEFAULTS["diag"].get("clientlog") is True)
    if logtxt() is None:
        ok(f"Server-Log {LOG} lesbar (Server mit Ausgabe in STATE_DIR/server.log starten)", False)
        return

    # --- Rechte -------------------------------------------------------------------------------------------------------
    good = head(items=[item()])
    s, _ = req("POST", "/api/client/log", None, good)
    ok(f"ohne Anmeldung: 401 ({s})", s == 401)
    s, _ = req("POST", "/api/client/log", A, good, hdr={"X-Saga": "0"})
    ok(f"ohne X-Saga-Kopf: 401 ({s})", s == 401)
    s, _ = req("POST", "/api/client/diag", None, head(ev=[]))
    ok(f"Diagnose ohne Anmeldung: 401 ({s})", s == 401)
    s, _ = req("GET", "/api/client/log", A)
    ok(f"GET -> 405 ({s})", s == 405)

    # --- Pruefung (Konto D: Ratenbegrenzung zaehlt nur angenommene Berichte) ------------------------------------------
    n0 = len(logtxt() or "")
    bad = [("v=2", head(v=2, items=[item()])), ("app unbekannt", head(app="windows", items=[item()])),
           ("ver mit Leerzeichen", head(ver="2.5 beta", items=[item()])), ("sid zu kurz", head(sid="abc", items=[item()])),
           ("dev", head(dev="tv", items=[item()])), ("items leer", head(items=[])), ("items kein Array", head(items={})),
           ("21 items", head(items=[item()] * 21)), ("k unbekannt", head(items=[item(k="fatal")])),
           ("msg kein Text", head(items=[item(msg=5)])), ("msg > 4000", head(items=[item(msg="x" * 4001)])),
           ("line negativ", head(items=[item(line=-1)])), ("col Text", head(items=[item(col="5")])),
           ("t fehlt", head(items=[{k: v for k, v in item().items() if k != "t"}])), ("t Bool", head(items=[item(t=True)])),
           ("screen mit Titel", head(items=[item(screen="Mein Plan")])), ("screen mit ID", head(items=[item(screen="pages/12")])),
           ("n = 0", head(items=[item(n=0)])), ("kein Objekt", [1, 2])]
    codes = {n: req("POST", "/api/client/log", D, b)[0] for n, b in bad}
    ok(f"falsche Felder -> 400 ({[n for n, c in codes.items() if c != 400]})", all(c == 400 for c in codes.values()))
    s, _ = req("POST", "/api/client/log", D, raw="{kein json")
    ok(f"kein JSON -> 400 ({s})", s == 400)
    s, _ = req("POST", "/api/client/log", D, {"raw": "x" * (17 * 1024)})
    ok(f"Bericht > 16 KB -> 413 ({s})", s == 413)
    s, _ = req("POST", "/api/client/diag", D, head(ev=[], note="x" * 10, pad="y" * (65 * 1024)))
    ok(f"Diagnose > 64 KB -> 413 ({s})", s == 413)
    dbad = [("201 ev", head(ev=[{"t": 1, "k": "view", "screen": "pages"}] * 201)), ("ev k", head(ev=[{"t": 1, "k": "html"}])),
            ("net ohne m", head(ev=[{"t": 1, "k": "net", "p": "/api/x", "s": 200}])), ("net s > 999", head(ev=[{"t": 1, "k": "net", "m": "GET", "p": "/a", "s": 1000}])),
            ("conn ch", head(ev=[{"t": 1, "k": "conn", "ch": "irc", "st": "open"}])), ("save st", head(ev=[{"t": 1, "k": "save", "st": "maybe"}])),
            ("view mit Titel", head(ev=[{"t": 1, "k": "view", "screen": TITLE}])), ("note > 1000", head(ev=[], note="x" * 1001)),
            ("note kein Text", head(ev=[], note=5))]
    dcodes = {n: req("POST", "/api/client/diag", D, b)[0] for n, b in dbad}
    ok(f"Diagnose: falsche Felder -> 400 ({[n for n, c in dcodes.items() if c != 400]})", all(c == 400 for c in dcodes.values()))
    ok("abgelehnte POSTs schreiben keine Log-Zeile", not lines_since(n0, "clientlog") and not lines_since(n0, "clientdiag"))
    s, _ = req("POST", "/api/client/log", D, head(items=[item(t=i) for i in range(20)]))
    ok(f"Grenze genau: 20 Eintraege -> angenommen ({s})", s == 200)

    # --- Bereinigung, Log-Zeile ------------------------------------------------------------------------------------
    c0 = counts()
    n0 = len(logtxt() or "")
    probe = head(items=[
        item(msg=f"Error: Seite \"{TITLE}\" fuer {MAIL} nicht geladen von {URLP} (id {NUMP}, {UUID}, {HEXP}, {KEY}, {HANDLE})",
             stack=f"Error: Seite \"{TITLE}\" fehlt\n    at saveNow (http://127.0.0.1:8451/static/pages.js?v=abc1234567:120:15)\n"
                   f"    at async Object.flush (https://saga.example.org/static/pm-editor.js?v=9:9:3)\n"
                   f"    at https://evil-cdn.example.org/geheimordner/{TITLE.replace(' ', '%20')}.js:3:4\n"
                   f"    at chrome-extension://{HEXP}/content.js:1:2",
             src="http://127.0.0.1:8451/static/pages.js?v=abc1234567", n=3, extra="geheim-feld-xyz", title=TITLE),
        item(k="unhandled", msg="TypeError: Cannot read properties of undefined (reading 'title')", src=URLP, line=None, col=None),
        item(k="warn", msg="Script error.", src=None, line=None, col=None, screen="chat")], secret="geheim-feld-xyz")
    s, d = req("POST", "/api/client/log", A, probe)
    ok(f"Bericht mit Probe-Inhalten: 200 ok ({s}, {d})", s == 200 and d == {"ok": True})
    lines = lines_since(n0, "clientlog")
    ok(f"genau eine Zeile 'clientlog' ({len(lines)})", len(lines) == 1)
    ln = lines[0] if lines else ""
    rec = json.loads(ln[len("clientlog "):]) if ln else {}
    hits = [p for p in PROBES if p.lower() in ln.lower()]
    ok(f"keine Probe-Inhalte in der Log-Zeile ({hits})", not hits)
    ok("kein Konto-/Anzeigename, keine IP-Adresse, keine unbekannten Felder",
       not re.search(re.escape(PFX) + r"|Anna Probe|Probe\b", ln) and not re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", ln)
       and "geheim-feld" not in ln and '"title"' not in ln and '"secret"' not in ln)
    ok(f"Konto als Hash (10 Hex) ({rec.get('acct')})", re.fullmatch(r"[0-9a-f]{10}", rec.get("acct") or ""))
    it = (rec.get("items") or [{}])[0]
    ok(f"Platzhalter im Fehlertext ({it.get('msg')})", all(x in (it.get("msg") or "") for x in ("<str>", "<email>", "<url>", "<n>", "<uuid>", "<hex>", "<secret>", "<id>")))
    ok(f"Stack: Funktion + static/<datei>.js:Zeile:Spalte, fremde Quellen nur <ext> ({it.get('stack')!r})",
       "saveNow static/pages.js:120:15" in (it.get("stack") or "") and "Object.flush static/pm-editor.js:9:3" in (it.get("stack") or "")
       and (it.get("stack") or "").count("<ext>") == 2 and "?v=" not in (it.get("stack") or ""))
    ok(f"src ohne Host und ?v=, n, line/col, screen ({it.get('src')}, {it.get('n')}, {it.get('line')}, {it.get('screen')})",
       it.get("src") == "static/pages.js" and it.get("n") == 3 and it.get("line") == 12 and it.get("col") == 5 and it.get("screen") == "pages")
    it2 = (rec.get("items") or [{}, {}])[1]
    ok(f"Bezeichner in Zitaten bleiben ('title'), fremde src -> <ext> ({it2.get('msg')}, {it2.get('src')})",
       "(reading 'title')" in (it2.get("msg") or "") and it2.get("src") == "<ext>" and it2.get("k") == "unhandled")
    ok(f"Kopf: app, ver, os, dev, sid (16) ({rec.get('app')}, {rec.get('ver')}, {rec.get('os')}, {rec.get('dev')})",
       rec.get("app") == "web" and rec.get("ver") == "2.5.0-rc.9" and rec.get("os") == "Linux; Chrome 130" and rec.get("dev") == "desktop"
       and len(rec.get("sid") or "") <= 16)

    # --- Diagnose ---------------------------------------------------------------------------------------------------
    n0 = len(logtxt() or "")
    evs = [{"t": 10, "k": "life", "st": "start"}, {"t": 20, "k": "view", "screen": "pages"},
           {"t": 30, "k": "net", "m": "GET", "p": f"/api/notes/{NUMP}/comments?q={TITLE}", "s": 200, "ms": 41},
           {"t": 31, "k": "net", "m": "PATCH", "p": f"/api/notes/-{NUMP}", "s": 409},
           {"t": 32, "k": "net", "m": "GET", "p": f"/api/chats/12/messages/{HEXP}", "s": 500},
           {"t": 33, "k": "net", "m": "GET", "p": f"/@probe/{TITLE}", "s": 404},
           {"t": 40, "k": "save", "st": "conflict", "s": 409}, {"t": 50, "k": "conn", "ch": "sse", "st": "open"},
           {"t": 60, "k": "err", "kind": "error", "msg": f"Error: \u201e{TITLE}\u201c bei {MAIL}", "src": URLP, "line": 3, "col": 4, "screen": "pages"},
           {"t": 70, "k": "view", "screen": "settings", "title": TITLE}]
    note = f"Beim Speichern kam ein Fehler.\nMeine Adresse {MAIL}, Schluessel {KEY}"
    s, d = req("POST", "/api/client/diag", A, head(ev=evs, note=note))
    did = d.get("id") if isinstance(d, dict) else None
    ok(f"Diagnose: 200 mit Kennung D-XXXXX ({s}, {d})", s == 200 and d.get("ok") is True and re.fullmatch(r"D-[2-9A-HJKMNP-TV-Z]{5}", did or ""))
    dl = lines_since(n0, "clientdiag")
    ok(f"genau eine Zeile 'clientdiag' ({len(dl)})", len(dl) == 1)
    dln = dl[0] if dl else ""
    drec = json.loads(dln[len("clientdiag "):]) if dln else {}
    ok(f"dieselbe Kennung in der Log-Zeile ({drec.get('id')})", drec.get("id") == did)
    ok("dasselbe Konto-Hash wie im Fehlerbericht", drec.get("acct") == rec.get("acct"))
    dh = [p for p in PROBES if p.lower() in dln.lower()]
    ok(f"keine Probe-Inhalte in der Diagnose-Zeile ({dh})", not dh and not re.search(re.escape(PFX) + r"|Anna Probe", dln))
    paths = [e.get("p") for e in drec.get("ev") or [] if e.get("k") == "net"]
    ok(f"Endpunkt-Muster ({paths})", paths == ["/api/notes/:id/comments", "/api/notes/:id", "/api/chats/:id/messages/:x", "/:x/:x"])
    un = drec.get("user_note") or ""
    ok(f"Freitext als user_note, Geheimnisse/E-Mail maskiert ({un!r})",
       un.startswith("Beim Speichern kam ein Fehler.") and "<email>" in un and "<secret>" in un and "\n" not in un)
    ok(f"Ereignisse: Anzahl, unbekannte Felder verworfen ({drec.get('n')})", drec.get("n") == len(evs) and '"title"' not in dln)
    errev = [e for e in drec.get("ev") or [] if e.get("k") == "err"]
    ok(f"Fehler-Ereignis bereinigt ({errev})", errev and errev[0].get("src") == "<ext>" and "<email>" in errev[0].get("msg", ""))
    ok(f"Zeilenzahlen aller Tabellen unveraendert ({ {k: (c0[k], v) for k, v in counts().items() if c0.get(k) != v} })", counts() == c0)

    # --- Raten ------------------------------------------------------------------------------------------------------
    codes = [req("POST", "/api/client/log", B, head(items=[item()]))[0] for _ in range(11)]
    ok(f"10 Berichte je Minute je Konto, der 11. -> 429 ({codes})", codes[:10] == [200] * 10 and codes[10] == 429)
    s, d = req("POST", "/api/client/log", B, head(items=[item()]))
    ok(f"429 mit retry_s ({d})", s == 429 and isinstance(d, dict) and d.get("retry_s", 0) > 0)
    s, _ = req("POST", "/api/client/log", Cc, head(items=[item()]))
    ok(f"anderes Konto nicht betroffen ({s})", s == 200)
    dc = [req("POST", "/api/client/diag", B, head(ev=[]))[0] for _ in range(6)]
    ok(f"5 Diagnosen je Stunde je Konto, die 6. -> 429 ({dc})", dc[:5] == [200] * 5 and dc[5] == 429)

    # --- Admin-Schalter (echtes Formular) --------------------------------------------------------------------------
    adm = PFX + "adm"
    s, page = admin("GET", "/settings", adm)
    ok(f"Admin-Oberflaeche: Schalter diag_clientlog, an ({s})", s == 200 and re.search(r'name="diag_clientlog"[^>]*checked', page or ""))
    fp = FormParser()
    fp.feed(page or "")
    before = auth.settings(fresh=True)
    form = [(k, v) for k, v in fp.fields if k != "diag_clientlog"]
    s, _ = admin("POST", "/settings", adm, form)
    after = auth.settings(fresh=True)
    ok(f"Admin speichert ohne Haken: diag.clientlog aus ({s})", s == 303 and after["diag"].get("clientlog") is False)
    same = [k for k in ("title", "registration", "chat_enabled", "share_enabled", "publish_enabled", "calls", "api") if before.get(k) != after.get(k)]
    ok(f"uebrige Einstellungen unveraendert ({same})", not same)
    time.sleep(WAIT)
    ok("aus: Feature clientlog1 fehlt in /api/version", "clientlog1" not in feats())
    s1, _ = req("POST", "/api/client/log", Cc, head(items=[item()]))
    s2, _ = req("POST", "/api/client/diag", Cc, head(ev=[]))
    ok(f"aus: POST log/diag -> 404 ({s1}, {s2})", s1 == 404 and s2 == 404)
    s, _ = admin("POST", "/settings", adm, form + [("diag_clientlog", "1")])
    ok(f"Admin speichert mit Haken: wieder an ({s})", s == 303 and auth.settings(fresh=True)["diag"].get("clientlog") is True)
    time.sleep(WAIT)
    ok("wieder an: Feature clientlog1 zurueck", "clientlog1" in feats())

    # --- IP-Grenze (zuletzt: sperrt diese Adresse fuer eine Minute) -------------------------------------------------
    extra = []
    for i in range(14):
        u = f"{PFX}ip{i}"
        auth.create_user(u, None, u)
        extra.append(ck(u))
    ipc = [req("POST", "/api/client/log", k, head(items=[item()]))[0] for k in extra for _ in range(10)]
    ok(f"IP-Grenze: nach 120 Berichten je Minute von einer Adresse -> 429 ({ipc.count(200)} x 200, {ipc.count(429)} x 429)",
       200 in ipc and ipc[-1] == 429 and ipc.count(200) <= 120)


if __name__ == "__main__":
    main()
