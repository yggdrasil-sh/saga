"""Kollegin K-I (2.6, Bugs-DB 2269): Einwilligung, Anbieter-/Host-Hinweis, Ort "Saga" (Gespraech) und "Meine Daten".

Prueft (Server, laufende Instanz unter HK_TEST_BASE + Fake-Modell tests/fakeai.py auf HK_FAKEAI_PORT):
- Anbieter-Hinweis (M4, EV-28, RT-39): private Adresse -> "im Heimnetz"; bekannter Anbieter -> Host + Name + Land;
  oeffentliche IP mit Admin-Satz "lokal" (auch verschleiert) -> Satz faellt weg, nie "Heimnetz"; ohne festgehaltene IPs nie
  "Heimnetz"; gemischte IPs -> Internet; IP in der Adresse; Zusatzsatz = ein Satz, eine Zeile, hoechstens 200 Zeichen;
  Admin-Formular speichert ihn bereinigt; /api/ai/status traegt endpoint + intro (Text nennt "passende Ausschnitte aus
  deinen Seiten, Aufgaben und Terminen" und den Host des Modell-Endpunkts, nie die Saga-Domain).
- Einwilligung (K19): Zustand none -> later -> granted (Zeitpunkt, Fassung, Host) -> withdrawn; falsche Fassung 409;
  anderer Endpunkt -> stale (alte Einwilligung gilt nicht); ohne Faehigkeit ai 403; ohne Einwilligung geht im Ort "Saga"
  nichts ans Modell (403, Fake-Zaehler unveraendert) - RT-36-Teil; "Frag deine Seiten" bleibt wie bisher.
- RT-36: Widerruf haelt laufende Auftraege an (wartet_dich 'einwilligung'), danach kein Schritt und kein Modellaufruf,
  auch nicht nach "Fortsetzen"; jobs.consent_ok ist echt.
- Gespraech (K10): Zuege in ai_turn (nicht chat_msg), ohne Verlauf mit Ablauf 24 h, mit Verlauf ohne; fruehere Zuege gehen
  als Gespraech an das Modell; Leeren; cleanup entfernt Abgelaufenes; andere Person sieht nichts.
- Einstellungen (/api/ai/prefs): Ideen nur mit Einwilligung (409) und Faehigkeit ai_ideas (403); falsche Werte 400;
  Widerruf schaltet Ideen aus; ideas_ok.
- Meine Daten (M7, RT-35): Export JSON/Markdown vollstaendig (Einwilligungen, Gespraech, Auftraege mit Schritten/
  Protokoll, Verlauf, Zaehler) und nur eigene Daten; Loeschen nur mit Bestaetigung, entfernt Auftraege/Gespraech/
  Verlauf, laesst das Einwilligungs-Protokoll und andere Personen; Kontoloeschung raeumt ai_consent/ai_thread/ai_turn.
- Erste Schritte (2031): ohne KI genau die bisherigen Punkte, mit KI zusaetzlich "ai", erledigt mit der Einwilligung.
- Seitenleiste: /dashboard und /saga mit Eintrag "Saga" nur mit Faehigkeit ai; /saga ohne KI -> /dashboard.
Wegwerf-Konten kw-*; stellt die KI-Einstellung wieder her.
Aufruf (saga-test): PYTHONPATH=tests/pgshim:. python tests/kollegin_einwilligung.py"""
import base64
import http.client
import json
import os
import socket
import subprocess
import sys
import time
import urllib.parse

from itsdangerous import TimestampSigner

import auth
import db

db.wait_ready()
from db import connect  # noqa: E402

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8920"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
PORT = int(os.environ.get("HK_FAKEAI_PORT", "8925"))
FAKE = f"http://127.0.0.1:{PORT}"
WAIT = 5.6                       # Einstellungen sind im Server 5 s zwischengespeichert
HERE = os.path.dirname(os.path.abspath(__file__))
PW = "Kw-Pass-12345"
A, B, KID, OFF = "kw-anna", "kw-ben", "kw-kind", "kw-aus"
res = []


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, "" if c else str(info)[:500], flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, u=None, body=None, hdr=None, base=None, raw=False):
    base = base or B_
    c = http.client.HTTPConnection(base.hostname, base.port, timeout=30)
    h = {"Host": HOST, "Accept-Language": "de"} if base is B_ else {}
    data = None
    if u:
        h["Cookie"] = "saga=" + ck(u)
        h["X-Saga"] = "1"
    if body is not None:
        h["Content-Type"] = "application/json"
        data = json.dumps(body)
    h.update(hdr or {})
    c.request(m, p, body=data, headers=h)
    r = c.getresponse()
    out = r.read()
    hd = {k.lower(): v for k, v in r.getheaders()}
    c.close()
    if raw:
        return r.status, out.decode("utf-8", "replace"), hd
    try:
        out = json.loads(out)
    except ValueError:
        out = out.decode("utf-8", "replace")
    return r.status, out


def sse(path, u, body):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=40)
    h = {"Host": HOST, "Accept-Language": "de", "Content-Type": "application/json", "Accept": "text/event-stream",
         "Cookie": "saga=" + ck(u), "X-Saga": "1"}
    c.request("POST", path, body=json.dumps(body), headers=h)
    r = c.getresponse()
    if r.status != 200 or not (r.getheader("content-type") or "").startswith("text/event-stream"):
        d = r.read()
        c.close()
        try:
            d = json.loads(d)
        except ValueError:
            pass
        return r.status, d
    ev, cur = [], {}
    while True:
        line = r.readline()
        if not line:
            break
        line = line.decode("utf-8").rstrip("\r\n")
        if line == "":
            if "event" in cur:
                ev.append((cur["event"], json.loads(cur.get("data") or "null")))
            cur = {}
        elif not line.startswith(":"):
            k, _, v = line.partition(":")
            cur[k] = v[1:] if v.startswith(" ") else v
    c.close()
    return 200, ev


def get(ev, typ):
    for t_, d in ev if isinstance(ev, list) else []:
        if t_ == typ:
            return d
    return None


def text_of(ev):
    return "".join(d.get("t", "") for t_, d in ev if t_ == "delta") if isinstance(ev, list) else ""


def fake(m, p):
    return req(m, p, base=urllib.parse.urlparse(FAKE))[1]


def fcount():
    return (fake("GET", "/_count") or {}).get("n", -1)


def setai(wait=True, **kw):
    cur = dict(auth.settings(fresh=True)["ai"])
    cur.update(kw)
    auth.save_setting("ai", cur)
    auth.settings(fresh=True)
    if wait:
        time.sleep(WAIT)


def q(sql, args=()):
    with connect() as c:
        return c.execute(sql, args).fetchall()


def cleanup():
    from saga.userdata import delete_user_data
    for u in [r[0] for r in q("SELECT username FROM users WHERE username LIKE 'kw-%'")]:
        delete_user_data(u)
    with connect() as c, c:
        for t_ in ("job", "ai_turn", "ai_thread", "ai_consent", "ai_history", "ai_usage", "ai_units", "user_kv", "note", "task"):
            c.execute(f"DELETE FROM {t_} WHERE uid LIKE 'kw-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'kw-%'")
        c.execute("DELETE FROM groups WHERE name LIKE 'kw-%'")


def main():
    cleanup()
    prev = dict(auth.settings(fresh=True).get("ai") or {})
    prev_fs = auth.settings(fresh=True).get("first_steps", True)
    proc = subprocess.Popen([sys.executable, os.path.join(HERE, "fakeai.py"), str(PORT)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(80):
            try:
                socket.create_connection(("127.0.0.1", PORT), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        hint_cases()
        run()
    finally:
        proc.terminate()
        auth.save_setting("ai", prev)
        auth.save_setting("first_steps", prev_fs)
        auth.settings(fresh=True)
        cleanup()
    print(f"\nKollegin Einwilligung {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


# --- Anbieter-/Host-Hinweis (in-process, Sprache de) ---------------------------------------------------------------
def hint_cases():
    from saga import ai_saga, i18n
    tok = i18n.set_lang("de")
    try:
        def h(url, pinned=(), note=None):
            return ai_saga.endpoint_hint({"url": url, "pinned": list(pinned), "endpoint_note": note})
        x = h("http://ollama.lan:11434/v1", ["10.0.0.5"])
        ok("EV-28: private festgehaltene IP -> im Heimnetz, kein Anbieter",
           x["net"] == "home" and x["label"] == "ollama.lan:11434 (im Heimnetz)" and "provider" not in x, x)
        x = h("https://api.anthropic.com/v1", ["8.8.4.4"])
        ok("EV-28: bekannter Anbieter -> Host + Name + Land",
           x["net"] == "internet" and x.get("provider") == "Anthropic" and x.get("country") == "US"
           and x["label"] == "api.anthropic.com (Anthropic, USA)", x)
        x = h("https://kunde.openai.azure.com/openai", ["8.8.4.4"])
        ok("bekannter Anbieter ohne festes Land (Azure, je Region) -> Name, im Internet",
           x.get("provider") == "Microsoft Azure" and "country" not in x and "im Internet" in x["label"], x)
        for note in ("Läuft lokal bei uns.", "L o k a l und sicher.", "LOKÁL!", "lo​kal", "Bleibt im Heimnetz.",
                     "Runs locally on our own server.", "Never leaves the house.", "On-premise.", "Se ejecuta en local.",
                     "Работает локально.", "Alles bleibt zu Hause."):
            x = h("https://llm.example.org/v1", ["8.8.8.8"], note)
            if "note" in x or x["net"] != "internet" or "Heimnetz" in x["label"] or "lokal" in x["label"].lower():
                ok(f"RT-39: oeffentliche IP + Admin-Satz {note!r} -> Satz faellt weg, nie Heimnetz", False, x)
                break
        else:
            ok("RT-39: oeffentliche IP + 'lokal'-Saetze (11 Schreibweisen, de/en/es/ru) -> Satz faellt weg, Internet", True)
        x = h("https://llm.example.org/v1", ["8.8.8.8"], "Betrieben von unserem Verein.")
        ok("oeffentliche IP + harmloser Zusatzsatz bleibt, Hinweis 'im Internet'",
           x.get("note") == "Betrieben von unserem Verein." and x["label"] == "llm.example.org (im Internet)", x)
        x = h("http://ollama.lan:11434", ["10.0.0.5"], "Läuft lokal im Keller. Zweiter Satz! Dritter")
        ok("Heimnetz: 'lokal' darf stehen; nur der erste Satz", x.get("note") == "Läuft lokal im Keller.", x)
        x = h("https://llm.example.org/v1", [])
        ok("ohne festgehaltene IPs nie Heimnetz", x["net"] == "internet", x)
        x = h("https://llm.example.org/v1", ["10.0.0.5", "8.8.8.8"])
        ok("gemischte IPs (privat + oeffentlich) -> Internet", x["net"] == "internet", x)
        x = h("http://10.1.2.3:11434")
        y = h("https://8.8.8.8/v1")
        ok("IP in der Adresse: privat -> Heimnetz, oeffentlich -> Internet", x["net"] == "home" and y["net"] == "internet", (x, y))
        x = h("https://api.openai.com/v1", ["10.0.0.9"])
        ok("Anbieter-Name hinter privater Adresse (Proxy) -> Heimnetz, kein Anbieter behauptet",
           x["net"] == "home" and "provider" not in x, x)
        ok("ohne Adresse: kein Hinweis", ai_saga.endpoint_hint({"url": ""}) is None)
        n = ai_saga.clean_note("Zeile eins\nZeile zwei " + "x" * 400)
        ok("Zusatzsatz: eine Zeile, hoechstens 200 Zeichen", "\n" not in n and len(n) <= 200, n)
        import admin
        v, _e = admin._ai_from({"provider": "openai", "endpoint_note": "Läuft bei uns.\nNoch was. Und mehr."}, {})
        v2, _e = admin._ai_from({"provider": "openai"}, {"endpoint_note": "Alt."})
        ok("Admin-Formular: Zusatzsatz bereinigt gespeichert, fehlendes Feld behaelt den alten",
           v["endpoint_note"] == "Läuft bei uns." and v2["endpoint_note"] == "Alt.", (v.get("endpoint_note"), v2.get("endpoint_note")))
    finally:
        i18n._cur.reset(tok)


# --- Server -----------------------------------------------------------------------------------------------------------
def run():
    from saga import ai_saga, caps, jobs, userdata
    import kollegin_testarten as KT
    KT.register_all()
    for u in (A, B, KID, OFF):
        auth.create_user(u, PW, u)
    auth.save_setting("first_steps", True)
    auth.set_user_groups(auth.get_user(A)["id"], ["kw-familie"])
    auth.set_user_groups(auth.get_user(B)["id"], ["kw-familie"])
    auth.set_user_groups(auth.get_user(KID)["id"], ["kw-familie", "kw-kinder"])
    setai(enabled=True, provider="openai", url=FAKE + "/v1", model="fake", pinned=["127.0.0.1"], groups=["kw-familie"],
          caps={"ai_ideas": [], "ai_act": []}, quota=50, history=True, timeout=10, endpoint_note="Steht bei uns im Keller.")
    fake("POST", "/_reset")

    # --- Seitenleiste, Erste Schritte ------------------------------------------------------------------------------------
    st, html, _h = req("GET", "/dashboard", A, raw=True)
    st2, html2, _h = req("GET", "/dashboard", OFF, raw=True)
    ok("Heute mit KI: Eintrag 'Saga' in der Seitenleiste (und Platz fuer die Karte)",
       st == 200 and 'id="pg-nav-saga"' in html and 'id="hz-saga-intro"' in html, st)
    ok("Heute ohne KI (keine Gruppe): kein Eintrag, keine Karte", st2 == 200 and "pg-nav-saga" not in html2 and "hz-saga-intro" not in html2)
    st, html, _h = req("GET", "/saga", A, raw=True)
    st2, _b, hd2 = req("GET", "/saga", OFF, raw=True)
    ok("/saga: Seite mit Leiste B6 und Einhaengepunkt #sg-cards; ohne KI -> /dashboard",
       st == 200 and 'id="sg-bar"' in html and 'id="sg-cards"' in html and 'aria-current="page"' in html
       and st2 == 303 and hd2.get("location") == "/dashboard", (st, st2))
    st, fs = req("GET", "/api/first-steps", A)
    st2, fs2 = req("GET", "/api/first-steps", OFF)
    ka = [i["key"] for i in fs.get("items") or []]
    ko = [i["key"] for i in fs2.get("items") or []]
    ok("Erste Schritte ohne KI: genau die bisherigen Punkte (vollstaendig ohne KI)", "ai" not in ko and len(ko) >= 4, ko)
    ok("Erste Schritte mit KI: zusaetzlich 'ai' (noch offen)", ka == ko + ["ai"] and not fs["items"][-1]["done"], ka)

    # --- Status: Hinweis und Kennenlernen-Text ---------------------------------------------------------------------------
    st, s = req("GET", "/api/ai/status", A)
    ep, it, cs = s.get("endpoint") or {}, s.get("intro") or {}, s.get("consent") or {}
    ok("Status: endpoint {host, net, note, label} vom Server",
       ep.get("host") == f"127.0.0.1:{PORT}" and ep.get("net") == "home" and ep.get("note") == "Steht bei uns im Keller."
       and ep.get("label") == f"127.0.0.1:{PORT} (im Heimnetz)", ep)
    lines = it.get("lines") or []
    ok("Kennenlernen: drei Saetze; Satz 2 nennt Ausschnitte aus Seiten, Aufgaben und Terminen + Host des Modell-Endpunkts",
       len(lines) == 3 and "passende Ausschnitte aus deinen Seiten, Aufgaben und Terminen" in lines[1]
       and ep["label"] in lines[1] and HOST not in " ".join(lines) and "OK" in lines[2], lines)
    ok("Kennenlernen: Zusatzsatz getrennt, Ideen-Frage, Fassung, Details (ⓘ: nicht Ende-zu-Ende verschluesselt)",
       it.get("note") == "Steht bei uns im Keller." and it.get("question") and it.get("version") == ai_saga.CONSENT_V
       and any("Ende-zu-Ende" in d for d in it.get("details") or []) and it.get("bar", "").endswith(ep["label"]), it)
    ok("Einwilligung anfangs: none", cs.get("state") == "none", cs)

    # --- ohne Einwilligung: nichts ans Modell ----------------------------------------------------------------------------
    for u in (A, B):
        nid = req("POST", "/api/notes", u, {"title": "Zitronenfalter"})[1]["id"]
        req("PATCH", f"/api/notes/{nid}", u, {"body": json.dumps({"blocks": [{"id": "b0", "type": "paragraph",
            "data": {"text": "Der Zitronenfalter fliegt im März."}}]}), "format": "blocks", "rev": 0})
    time.sleep(1.2)                                       # Suchspeicher der Seiten
    n0 = fcount()
    st, d = sse("/api/ai/thread", A, {"q": "Wann fliegt der Zitronenfalter?"})
    ok("RT-36/K19: Gespraech ohne Einwilligung -> 403 consent=needed, kein Modellaufruf",
       st == 403 and isinstance(d, dict) and d.get("consent") == "needed" and fcount() == n0, (st, d))
    ok("jobs.consent_ok echt: ohne Einwilligung False", jobs.consent_ok(A) is False)
    st, d = req("POST", "/api/ai/consent", A, {"answer": "later"})
    ok("'Jetzt nicht' -> later (keine Einwilligung)", st == 200 and d["consent"]["state"] == "later" and not ai_saga.consent_ok(A), d)
    st, d = sse("/api/ai/thread", A, {"q": "Wann fliegt der Zitronenfalter?"})
    ok("nach 'Jetzt nicht' weiter 403", st == 403 and fcount() == n0, st)
    st, d = req("POST", "/api/ai/consent", A, {"answer": "ideas", "version": "alt-0"})
    ok("falsche Fassung des Textes -> 409 mit aktueller Fassung", st == 409 and d.get("version") == ai_saga.CONSENT_V, (st, d))
    st, d = req("POST", "/api/ai/consent", A, {"answer": "vielleicht"})
    ok("unbekannte Antwort -> 400", st == 400, st)
    st, d = req("POST", "/api/ai/consent", OFF, {"answer": "ideas", "version": ai_saga.CONSENT_V})
    ok("ohne Faehigkeit ai -> 403", st == 403, st)
    st, d = req("POST", "/api/ai/consent", A, {"answer": "ideas", "version": ai_saga.CONSENT_V})
    c1 = d.get("consent") or {}
    ok("'Ja, mit Ideen' -> granted (Zeitpunkt, Fassung, Host), Ideen 'today'",
       st == 200 and c1.get("state") == "granted" and c1.get("version") == ai_saga.CONSENT_V and c1.get("host") == "127.0.0.1"
       and abs(c1.get("at", 0) - time.time()) < 60 and (d.get("prefs") or {}).get("ideas") == "today", d)
    row = q("SELECT action, version, host, net FROM ai_consent WHERE uid=? ORDER BY id", (A,))
    ok("Protokoll ai_consent: later, grant (mit Art des Endpunkts)", [r[0] for r in row] == ["later", "grant"] and row[-1][3] == "home", row)
    ok("jobs.consent_ok und ideas_ok: True", jobs.consent_ok(A) and ai_saga.ideas_ok(A))
    st, fs = req("GET", "/api/first-steps", A)
    ok("Erste Schritte: 'ai' erledigt nach der Einwilligung", (fs.get("items") or [{}])[-1] == {"key": "ai", "done": True}, fs.get("items"))

    # --- Gespraech ---------------------------------------------------------------------------------------------------------
    req("PUT", "/api/ai/prefs", A, {"history": False})
    nchat = (q("SELECT COUNT(*) FROM chat")[0][0], q("SELECT COUNT(*) FROM chat_msg")[0][0])
    st, ev = sse("/api/ai/thread", A, {"q": "Wann fliegt der Zitronenfalter?"})
    dn = get(ev, "done") or {}
    ok("Gespraech: Antwort als SSE (sources/delta/done) mit Nummer des Zugs", st == 200 and dn.get("turn") == 2 and text_of(ev), (st, ev))
    rows = q("SELECT n, role, expires, created FROM ai_turn WHERE uid=? ORDER BY n", (A,))
    ok("Zuege in ai_turn (me, ai), ohne Verlauf mit Ablauf +24 h", [r[1] for r in rows] == ["me", "ai"]
       and all(r[2] and abs(r[2] - r[3] - 86400) < 2 for r in rows), rows)
    ok("kein E2E-Chat: chat und chat_msg unveraendert (Ort 'Saga' hat eigene Tabellen)",
       q("SELECT COUNT(*) FROM chat")[0][0] == nchat[0] and q("SELECT COUNT(*) FROM chat_msg")[0][0] == nchat[1], nchat)
    st, ev = sse("/api/ai/thread", A, {"q": "Und im Herbst?"})
    body = (fake("GET", "/_last") or {}).get("body") or {}
    msgs = body.get("messages") or []
    ok("Rueckbezug: fruehere Zuege gehen als Gespraech an das Modell (system, user, assistant, user)",
       [m["role"] for m in msgs][:4] == ["system", "user", "assistant", "user"] and "Zitronenfalter" in msgs[1]["content"], [m["role"] for m in msgs])
    st, th = req("GET", "/api/ai/thread", A)
    st2, thb = req("GET", "/api/ai/thread", B)
    ok("GET /api/ai/thread: 4 Zuege, Verlauf aus, keep 86400; andere Person sieht nichts",
       len(th.get("turns") or []) == 4 and th.get("history") is False and th.get("keep") == 86400 and thb.get("turns") == [], (th, thb))
    req("PUT", "/api/ai/prefs", A, {"history": True})
    sse("/api/ai/thread", A, {"q": "Wie heisst der Falter auf Latein?"})
    rows = q("SELECT n, expires FROM ai_turn WHERE uid=? ORDER BY n", (A,))
    ok("Verlauf an: neue Zuege ohne Ablauf", rows[-1][1] is None and rows[-2][1] is None and rows[0][1] is not None, rows)
    req("PUT", "/api/ai/prefs", A, {"history": False})
    rows = q("SELECT n, expires FROM ai_turn WHERE uid=? ORDER BY n", (A,))
    ok("Verlauf aus: alle Zuege laufen nach 24 h ab", rows and all(r[1] for r in rows), rows)
    with connect() as c, c:
        c.execute("UPDATE ai_turn SET expires=? WHERE uid=? AND n=1", (time.time() - 1, A))
    gone = ai_saga.cleanup()
    ok("cleanup: abgelaufener Zug weg, Rest bleibt", gone >= 1 and [r[0] for r in q("SELECT n FROM ai_turn WHERE uid=? ORDER BY n", (A,))][0] == 2)
    jobs.cleanup()
    ok("jobs.cleanup ruft ai_saga.cleanup (ohne Fehler)", True)
    st_b, ev_b = sse("/api/ai/ask", B, {"q": "Wann fliegt der Zitronenfalter?"})
    ok("'Frag deine Seiten' wie bisher: ohne Einwilligung erlaubt (Faehigkeit ai + Hinweis)", st_b == 200 and get(ev_b, "done") is not None, st_b)

    # --- Einstellungen ------------------------------------------------------------------------------------------------------
    st, p = req("GET", "/api/ai/prefs", A)
    ok("GET /api/ai/prefs: ideas, sources, history, notify_content, salutation, dictation, consent",
       st == 200 and p.get("ideas") == "today" and set(p.get("sources") or {}) == {"pages", "tasks", "calendar", "chat"}
       and p.get("notify_content") == "unlocked" and p.get("salutation") == "du" and p.get("dictation") == "server"
       and (p.get("consent") or {}).get("state") == "granted", p)
    st, _d = req("PUT", "/api/ai/prefs", A, {"ideas": "immer"})
    st2, _d = req("PUT", "/api/ai/prefs", A, {"sources": {"pages": "ja"}})
    st3, d3 = req("PUT", "/api/ai/prefs", A, {"salutation": "sie", "sources": {"calendar": False}, "notify_content": "never"})
    ok("PUT /api/ai/prefs: falsche Werte 400, gueltige werden gespeichert",
       st == 400 and st2 == 400 and st3 == 200 and d3.get("salutation") == "sie" and d3["sources"]["calendar"] is False
       and d3.get("notify_content") == "never", (st, st2, d3))
    st, d = req("PUT", "/api/ai/prefs", B, {"ideas": "today"})
    ok("Ideen ohne Einwilligung -> 409", st == 409, (st, d))
    setai(caps={"ai_ideas": ["kw-niemand"], "ai_act": []})
    req("POST", "/api/ai/consent", B, {"answer": "ideas", "version": ai_saga.CONSENT_V})
    st, d = req("PUT", "/api/ai/prefs", B, {"ideas": "today"})
    st_s, s_b = req("GET", "/api/ai/status", B)
    ok("ohne Faehigkeit ai_ideas: 'Ja, mit Ideen' setzt keine Ideen, PUT 403, Karte ohne Ideen-Frage",
       st == 403 and (req("GET", "/api/ai/prefs", B)[1] or {}).get("ideas") == "off" and (s_b.get("intro") or {}).get("question") is None
       and (s_b.get("intro") or {}).get("ideas_allowed") is False, (st, d, s_b.get("intro")))
    setai(caps={"ai_ideas": [], "ai_act": []})

    # --- RT-36: Widerruf waehrend eines laufenden Auftrags ------------------------------------------------------------
    far = time.time() + 3600
    j1 = jobs.create(A, "test.model", steps=[{"tool": "model", "args": {}}])
    with connect() as c, c:                               # "laeuft", aber erst spaeter faellig: der Worker des Servers holt ihn nicht
        c.execute("UPDATE job SET state='laeuft', next_at=?, updated=? WHERE id=?", (far, time.time(), j1))
    j2 = jobs.create(A, "test.model", steps=[{"tool": "model", "args": {}}])
    n0, m0 = fcount(), KT.CALLS["model"]
    st, d = req("DELETE", "/api/ai/consent", A)
    ok("RT-36: Widerruf -> withdrawn, laufender Auftrag angehalten (paused 1)",
       st == 200 and d["consent"]["state"] == "withdrawn" and d.get("paused") == 1, d)
    s1 = jobs.state_of(j1)
    ok("RT-36: Auftrag wartet_dich 'einwilligung'", s1["state"] == "wartet_dich" and s1["reason"] == "einwilligung", s1)
    jobs.resume(j1, A)
    s2 = jobs.state_of(j2)
    jobs.approve(j2, A, s2["plan_v"], s2["plan_hash"])
    for _ in range(3):
        KT.run_all()
        time.sleep(0.3)
    s1, s2 = jobs.state_of(j1), jobs.state_of(j2)
    ok("RT-36: nach Fortsetzen/Freigabe ohne Einwilligung kein Schritt, kein Modellaufruf (wartet_dich 'einwilligung')",
       s1["state"] == "wartet_dich" and s1["reason"] == "einwilligung" and s2["state"] == "wartet_dich" and s2["reason"] == "einwilligung"
       and KT.CALLS["model"] == m0 and fcount() == n0, (s1, s2, KT.CALLS, fcount(), n0))
    ok("Widerruf: Ideen aus, consent_ok False, Gespraech wieder 403",
       (req("GET", "/api/ai/prefs", A)[1] or {}).get("ideas") == "off" and not jobs.consent_ok(A)
       and sse("/api/ai/thread", A, {"q": "Hallo?"})[0] == 403)
    st, d = req("POST", "/api/ai/consent", A, {"answer": "no_ideas", "version": ai_saga.CONSENT_V})
    ok("erneut kennenlernen -> granted, Ideen bleiben aus", d["consent"]["state"] == "granted" and d["prefs"]["ideas"] == "off", d)

    # --- anderer Endpunkt: alte Einwilligung gilt nicht ---------------------------------------------------------------------
    setai(url=f"http://localhost:{PORT}/v1", pinned=["127.0.0.1"])
    st, s = req("GET", "/api/ai/status", A)
    ok("Endpunkt gewechselt -> stale, consent_ok False (Karte erneut, Auftraege halten an)",
       (s.get("consent") or {}).get("state") == "stale" and not ai_saga.consent_ok(A), s.get("consent"))
    setai(url=FAKE + "/v1", pinned=["127.0.0.1"])
    ok("zurueck zum alten Endpunkt -> wieder granted", ai_saga.consent(A)["state"] == "granted")

    # --- Meine Daten: Export ---------------------------------------------------------------------------------------------
    with connect() as c, c:
        c.execute("INSERT INTO ai_history (uid, created, kind, question, answer, sources) VALUES (?,?,?,?,?,?)",
                  (A, time.time(), "ask", "Frage im Verlauf", "Antwort im Verlauf", "{}"))
    jb = jobs.create(B, "test.echo", title="Auftrag von Ben", steps=[{"tool": "echo", "args": {"x": 1}}])
    st, d = req("GET", "/api/ai/mydata?format=json", A)
    st0, _x = req("GET", "/api/ai/mydata?format=json", None)
    ok("Export ohne Anmeldung/X-Saga -> 401", st0 == 401, st0)
    acts = [x["action"] for x in d.get("consent") or []]
    jids = [j["id"] for j in d.get("jobs") or []]
    ok("RT-35 Export JSON: Einwilligungen (later, grant, withdraw, grant), Gespraech, Auftraege mit Schritten/Protokoll, Verlauf, Zaehler",
       st == 200 and acts == ["grant", "withdraw", "grant", "later"] and len(d.get("conversation") or []) >= 3
       and j1 in jids and j2 in jids and any(j["steps"] and j["events"] for j in d["jobs"])
       and d["history"] and d["history"][0]["question"] == "Frage im Verlauf"
       and d["counters"]["requests"] and d["prefs"]["sources"]["calendar"] is False, {k: (v if k != "jobs" else len(v)) for k, v in d.items()} if isinstance(d, dict) else d)
    ok("Export nur eigene Daten (Bens Auftrag fehlt)", jb not in jids and "Auftrag von Ben" not in json.dumps(d, ensure_ascii=False))
    st, md, hd = req("GET", "/api/ai/mydata?format=md", A, raw=True)
    ok("Export Markdown: Datei mit Ueberschriften, Gespraech, Verlauf",
       st == 200 and hd.get("content-type", "").startswith("text/markdown") and "attachment" in hd.get("content-disposition", "")
       and "## Einwilligungen" in md and "## Gespräch" in md and "Und im Herbst?" in md and "Frage im Verlauf" in md, (st, md[:300]))
    st, _x = req("GET", "/api/ai/mydata?format=pdf", A)
    ok("Export: unbekanntes Format 400", st == 400, st)

    # --- Meine Daten: Loeschen -------------------------------------------------------------------------------------------
    st, _x = req("POST", "/api/ai/mydata/delete", A, {})
    ok("Alles loeschen ohne Bestaetigung -> 400, nichts weg", st == 400 and q("SELECT 1 FROM job WHERE uid=?", (A,)), st)
    sse("/api/ai/thread", B, {"q": "Wann fliegt der Zitronenfalter?"})
    st, d = req("POST", "/api/ai/mydata/delete", A, {"confirm": True})
    ok("Alles loeschen: Auftraege, Gespraech, Verlauf weg",
       st == 200 and d["deleted"]["jobs"] >= 2 and not q("SELECT 1 FROM job WHERE uid=?", (A,))
       and not q("SELECT 1 FROM ai_turn WHERE uid=?", (A,)) and not q("SELECT 1 FROM ai_history WHERE uid=?", (A,))
       and not q("SELECT 1 FROM job_event WHERE job_id IN (?, ?)", (j1, j2)), d)
    ok("Loeschen laesst Einwilligungs-Protokoll und andere Personen",
       len(q("SELECT 1 FROM ai_consent WHERE uid=?", (A,))) == 4 and q("SELECT 1 FROM job WHERE id=?", (jb,))
       and q("SELECT 1 FROM ai_turn WHERE uid=?", (B,)) and ai_saga.consent_ok(A))

    # --- Kontoloeschung ------------------------------------------------------------------------------------------------------
    userdata.delete_user_data(B)
    ok("Kontoloeschung: ai_consent, ai_thread, ai_turn der Person weg (USER_TABLES)",
       not q("SELECT 1 FROM ai_consent WHERE uid=?", (B,)) and not q("SELECT 1 FROM ai_thread WHERE uid=?", (B,))
       and not q("SELECT 1 FROM ai_turn WHERE uid=?", (B,)) and q("SELECT 1 FROM ai_consent WHERE uid=?", (A,)))
    ok("Faehigkeit: caps.can(kw-kind, ai) mit Familie", caps.can(KID, "ai"))


# --- Hilfen fuer tests/browser_kollegin_kennenlernen.js ---------------------------------------------------------------
BA, BO = "kw-b-an", "kw-b-aus"
SAVE = os.path.join(os.environ.get("STATE_DIR", "/tmp"), "kw-browser-prev.json")


def browser_setup():
    """Konten kw-b-an (KI an, Gruppe kw-bfam) und kw-b-aus (ohne KI), KI auf das Fake-Modell, Persona "Marin", eine Seite
    und ein laufender (spaeter faelliger) Auftrag fuer kw-b-an -> JSON {job} auf stdout."""
    from saga import jobs
    cleanup()
    st = auth.settings(fresh=True)
    if not os.path.exists(SAVE):
        with open(SAVE, "w") as f:
            json.dump({"ai": st.get("ai") or {}, "ai_persona": st.get("ai_persona") or {}, "first_steps": st.get("first_steps", True)}, f)
    for u in (BA, BO):
        auth.create_user(u, PW, u)
    auth.set_user_groups(auth.get_user(BA)["id"], ["kw-bfam"])
    auth.save_setting("first_steps", True)
    auth.save_setting("ai_persona", {"name": "Marin"})
    setai(wait=False, enabled=True, provider="openai", url=FAKE + "/v1", model="fake", pinned=["127.0.0.1"], groups=["kw-bfam"],
          caps={"ai_ideas": [], "ai_act": []}, quota=50, history=True, timeout=10, endpoint_note="")
    j = jobs.create(BA, "test.echo", title="Grillkohle", steps=[{"tool": "echo", "args": {"x": 1}}])
    with connect() as c, c:
        c.execute("UPDATE job SET state='laeuft', next_at=?, updated=? WHERE id=?", (time.time() + 86400, time.time(), j))
    print(json.dumps({"job": j, "a": BA, "off": BO, "pw": PW}))


def browser_teardown():
    cleanup()
    if os.path.exists(SAVE):
        with open(SAVE) as f:
            prev = json.load(f)
        auth.save_setting("ai", prev["ai"])
        auth.save_setting("ai_persona", prev["ai_persona"])
        auth.save_setting("first_steps", prev["first_steps"])
        os.remove(SAVE)
    print("{}")


def job_state(jid):
    from saga import jobs
    s = jobs.state_of(int(jid)) or {}
    print(json.dumps({"state": s.get("state"), "reason": s.get("reason")}))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--browser-setup":
        browser_setup()
    elif len(sys.argv) > 1 and sys.argv[1] == "--browser-teardown":
        browser_teardown()
    elif len(sys.argv) > 2 and sys.argv[1] == "--job-state":
        job_state(sys.argv[2])
    else:
        main()
