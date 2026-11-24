"""Kollegin: Rechte des Kontext-Ausweises (Saga 2.6 K-A4, Bugs-DB 2266, Konzept K8, RT-41) - K-A-Teil der Reihe.

Tabellengesteuert ueber saga/ai_ctx.select (im Prozess, echte DB) und die KI-Endpunkte (Fake-Modell tests/fakeai.py):
- Papierkorb, Archiv und Vorlagen nie (eigene und geteilte), Canary-Seiten von B nie bei A, foederierte Canary nie
  (Eigentuemer '@name@host', negative Seiten-ID), Gaeste anderer Server ('@name@host') bekommen keinen Kontext.
- Schalter: eine Quelle wirkt nur, wenn Anfrage UND Person sie einschalten (pages/tasks/calendar/chat); Chat-Verlauf
  nur auf dem Weg chat (RT-41), der Chat-Weg bekommt nie Seiten/Aufgaben/Termine.
- read: nur Kennungen (note:<id>, tasks:open, cal:<datum>/<tage>, chat:<id>:<anzahl>) - in 'done', im Verlauf (nur
  wenn der Verlauf an ist) und nie mit Text: Canary-Woerter weder in der DB ausserhalb der Seiten noch im Server-Log.
- API der Personen-Schalter: GET /api/ai/status {sources}, PUT /api/ai/settings {sources} (history bleibt unberuehrt).

Aufruf (saga-test): tools/testumgebung/umgebung.sh lauf server <slot> kollegin_rechte.py
Wegwerf-Konten kr-*; raeumt auf."""
import json, os, re, socket, subprocess, sys, time
import auth
from db import connect

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chat as CT  # noqa: E402
import ki as K  # noqa: E402

from saga import ai_ctx  # noqa: E402

FED = "@eve@fremd.example"
GAST = "@gast@fremd.example"
READ_RE = re.compile(r"^(note:\d+|tasks:open|cal:\d{4}-\d{2}-\d{2}/\d+|chat:\d+:\d+)$")
CAN = {                               # Canary-Woerter (je eines je Seite) - nur in note/note_version erlaubt
    "a_own": "KANARIEeigenAx1", "a_trash": "KANARIEpapierkorbAx2", "a_arch": "KANARIEarchivAx3", "a_tpl": "KANARIEvorlageAx4",
    "b_priv": "KANARIEprivatBx5", "b_shared": "KANARIEgeteiltBx6", "b_sh_trash": "KANARIEgeteiltPapierkorbBx7",
    "b_sh_arch": "KANARIEgeteiltArchivBx8", "fed": "KANARIEfoederiertEx9", "task": "KANARIEaufgabeAx10",
    "chat": "KANARIEchatverlaufAx11",
}
NEVER = ("a_trash", "a_arch", "a_tpl", "b_priv", "b_sh_trash", "b_sh_arch", "fed")       # nie bei Anna
PAGES = ("a_own", "a_trash", "a_arch", "a_tpl", "b_priv", "b_shared", "b_sh_trash", "b_sh_arch", "fed")
Q = "Was weiss ich ueber Zebrafinken?"


def ok(n, c, info=""):
    K.ok(n if c else f"{n} {str(info)[:500]}", c)


def ids_of(cx):
    return {int(x.split(":")[1]) for x in cx.read if x.startswith("note:")}


def text_of(cx):
    return "\n".join([cx.text, cx.extra, json.dumps(cx.origins, ensure_ascii=False), json.dumps(cx.history, ensure_ascii=False)])


def zpage(cookie, title, canary):
    return K.page(cookie, f"Zebrafinken {title}", f"Die Zebrafinken brauchen Futter. {canary}")


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'kr-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM chat WHERE created_by LIKE 'kr-%' OR id IN (SELECT chat_id FROM chat_member WHERE uid LIKE 'kr-%')")
        for t in ("note", "user_kv", "task", "ai_history", "ai_usage", "notification", "chat_identity", "chat_device", "chat_ai_reply"):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE 'kr-%'")
        c.execute("DELETE FROM note WHERE uid=?", (FED,))
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'kr-%' OR target LIKE 'kr-%' OR owner_uid=?", (FED,))
        c.execute("DELETE FROM users WHERE username LIKE 'kr-%'")


def main():
    cleanup()
    prev = auth.settings(fresh=True)
    pv = dict(ai=dict(prev["ai"]), persona=dict(prev.get("ai_persona") or {}), chat=prev.get("chat_enabled", True),
              share=prev.get("share_enabled", True))
    auth.save_setting("chat_enabled", True)
    auth.save_setting("share_enabled", True)
    auth.save_setting("ai_persona", {"name": "Marin", "tone": ""})
    auth.save_setting("ai", {"enabled": True, "provider": "openai", "url": K.FAKE + "/v1", "model": "fake", "api_key": "", "groups": [],
                             "timeout": 10, "quota": 0, "context": 8000, "history": True, "pinned": ["127.0.0.1"], "chat": True,
                             "web": False})
    K.key_raw("")
    proc = subprocess.Popen([sys.executable, os.path.join(K.HERE, "fakeai.py"), str(K.PORT)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", K.PORT), timeout=0.2).close(); break
            except OSError:
                time.sleep(0.1)
        run()
    finally:
        proc.terminate()
        for s in CT.STREAMS:
            s.close()
        auth.save_setting("ai", pv["ai"])
        K.key_raw(pv["ai"].get("api_key") or "")
        auth.save_setting("ai_persona", pv["persona"])
        auth.save_setting("chat_enabled", pv["chat"])
        auth.save_setting("share_enabled", pv["share"])
        cleanup()
    print(f"\nKollegin Rechte (K-A4) {sum(K.res)}/{len(K.res)}", "- ALLES GRUEN" if all(K.res) else "- FEHLER")
    sys.exit(0 if all(K.res) else 1)


def run():
    auth.create_user("kr-a", CT.PW, "Anna KR")
    auth.create_user("kr-b", CT.PW, "Bert KR")
    time.sleep(K.WAIT)
    A, Bk = K.ck("kr-a"), K.ck("kr-b")
    P = {}
    P["a_own"] = zpage(A, "eigene Notiz", CAN["a_own"])
    for k, title in (("a_trash", "Papierkorb"), ("a_arch", "Archiv"), ("a_tpl", "Vorlage")):
        P[k] = zpage(A, title, CAN[k])
    K.req("DELETE", f"/api/notes/{P['a_trash']}", A)
    K.req("POST", f"/api/notes/{P['a_arch']}/archive", A, {"archived": True})
    K.req("PATCH", f"/api/notes/{P['a_tpl']}", A, {"template": True})
    P["b_priv"] = zpage(Bk, "privat von Bert", CAN["b_priv"])
    P["b_shared"] = zpage(Bk, "geteilt von Bert", CAN["b_shared"])
    for k in ("b_sh_trash", "b_sh_arch"):
        P[k] = K.req("POST", "/api/notes", Bk, {"title": f"Zebrafinken {k}", "parent_id": P["b_shared"]})[1]["id"]
        K.req("PATCH", f"/api/notes/{P[k]}", Bk, {"body": K.blocks(f"Zebrafinken unter der geteilten Seite. {CAN[k]}"),
                                                   "format": "blocks", "rev": 0})
    K.req("DELETE", f"/api/notes/{P['b_sh_trash']}", Bk)
    K.req("POST", f"/api/notes/{P['b_sh_arch']}/archive", Bk, {"archived": True})
    s, _ = K.req("PUT", f"/api/notes/{P['b_shared']}/shares", Bk, {"type": "user", "target": "kr-a", "role": "view"})
    ok(f"Bert teilt eine Seite (mit Unterseiten) mit Anna ({s})", s == 200)
    with connect() as c, c:                  # foederierte Canary: Eigentuemer '@eve@fremd.example' mit Freigabe an Anna
        now = time.time()
        cur = c.execute("INSERT INTO note (uid, title, body, created, updated, format) VALUES (?,?,?,?,?,?)",
                        (FED, "Zebrafinken von Eve", K.blocks(f"Zebrafinken aus der Ferne. {CAN['fed']}"), now, now, "blocks"))
        P["fed"] = cur.lastrowid
        c.execute("INSERT INTO note_share (note_id, owner_uid, target_type, target, role, created) VALUES (?,?,?,?,?,?)",
                  (P["fed"], FED, "user", "kr-a", "view", now))
    K.req("POST", "/api/tasks", A, {"text": f"Zebrafinken fuettern {CAN['task']}"})

    # --- Matrix ueber ai_ctx.select ---------------------------------------------------------------------------------
    hist = [{"name": "Bert KR", "text": f"Zebrafinken {CAN['chat']}"}]
    all_on = {"pages": True, "tasks": True, "calendar": True, "chat": True}
    M = [  # (fall, uid, way, want, history, chat_id, erwartete note-Seiten, erwartete andere Kennungen)
        ("Frage: eigene + geteilte, nie Papierkorb/Archiv/Vorlage/fremd/foederiert", "kr-a", "ask", {"pages": True}, None, None,
         {"a_own", "b_shared"}, set()),
        ("Frage mit Aufgaben und Kalender", "kr-a", "ask", all_on, None, None, {"a_own", "b_shared"}, {"tasks:open", "cal"}),
        ("Frage: Verlauf mitgeschickt -> nie benutzt (RT-41)", "kr-a", "ask", all_on, hist, 7, {"a_own", "b_shared"}, {"tasks:open", "cal"}),
        ("Auftrag (job): wie Frage, ohne Verlauf", "kr-a", "job", all_on, hist, 7, {"a_own", "b_shared"}, {"tasks:open", "cal"}),
        ("Idee (idea): wie Frage, ohne Verlauf", "kr-a", "idea", {"pages": True}, hist, 7, {"a_own", "b_shared"}, set()),
        ("Frage ohne Anfrage-Schalter fuer Seiten", "kr-a", "ask", {"tasks": True}, None, None, set(), {"tasks:open"}),
        ("Chat: nur Verlauf, nie Seiten/Aufgaben/Termine", "kr-a", "chat", all_on, hist, 7, set(), {"chat:7:1"}),
        ("Chat ohne Anfrage-Schalter chat", "kr-a", "chat", {"pages": True}, hist, 7, set(), set()),
        ("Bert: nur eigene Seiten (Annas nie)", "kr-b", "ask", {"pages": True}, None, None, {"b_priv", "b_shared"}, set()),
        ("Gast anderer Server: kein Kontext", GAST, "ask", all_on, hist, 7, set(), set()),
        ("Gast anderer Server: kein Chat-Verlauf", GAST, "chat", all_on, hist, 7, set(), set()),
    ]
    name_of = {v: k for k, v in P.items()}
    for fall, uid, way, want, h, cid, pages, other in M:
        cx = ai_ctx.select(uid, Q, way=way, budget=8000, want=want, chat_id=cid, history=h)
        got_pages = {name_of.get(i, i) for i in ids_of(cx)}
        got_other = {("cal" if x.startswith("cal:") else x) for x in cx.read if not x.startswith("note:")}
        txt = text_of(cx)
        leak = [k for k in PAGES if k not in pages and CAN[k] in txt]      # jede Seite, die nicht erwartet ist
        ok(f"{fall}: read Seiten {sorted(got_pages)}", got_pages == pages, sorted(got_pages))
        ok(f"{fall}: read sonst {sorted(got_other)}", got_other == other, cx.read)
        ok(f"{fall}: kein Canary anderer Seiten (Papierkorb/Archiv/Vorlage/fremd/foederiert) im Kontext", not leak, leak)
        ok(f"{fall}: read nur Kennungen", all(isinstance(x, str) and READ_RE.match(x) for x in cx.read), cx.read)
        if way != "chat" or "chat" not in want:
            ok(f"{fall}: kein Chat-Verlauf", not cx.history and CAN["chat"] not in txt, cx.history)
        if way == "chat":
            ok(f"{fall}: keine Seiten, Aufgaben, Termine", not cx.text and not cx.extra and CAN["task"] not in txt and CAN["a_own"] not in txt)
    cx = ai_ctx.select("kr-a", "", way="page", budget=0, note_id=-(5 * 1024 + 3))
    ok(f"Seite: negative (foederierte) ID -> 404, nichts gelesen ({getattr(cx.error, 'status_code', None)})",
       getattr(cx.error, "status_code", None) == 404 and cx.read == [] and cx.page is None)
    cx = ai_ctx.select("kr-a", "", way="page", budget=0, note_id=P["fed"])
    ok(f"Seite: Seite mit foederiertem Eigentuemer -> 404 ({getattr(cx.error, 'status_code', None)})",
       getattr(cx.error, "status_code", None) == 404 and cx.page is None)
    for k in ("a_trash", "b_sh_trash", "b_priv"):
        cx = ai_ctx.select("kr-a", "", way="page", budget=0, note_id=P[k])
        ok(f"Seite: {k} -> nicht lesbar ({getattr(cx.error, 'status_code', None)})", cx.error is not None and cx.page is None and cx.read == [])
    cx = ai_ctx.select("kr-a", "", way="page", budget=0, note_id=P["b_shared"])
    ok(f"Seite: geteilte Seite lesbar, read = genau diese Seite ({cx.read})", cx.error is None and cx.read == [f"note:{P['b_shared']}"])
    cx = ai_ctx.select(GAST, "", way="page", budget=0, note_id=P["b_shared"])
    ok("Seite: Gast anderer Server -> 404", getattr(cx.error, "status_code", None) == 404 and cx.page is None)
    try:
        ai_ctx.select("kr-a", Q, way="anrede", budget=8000)
        ok("Unbekannter Weg (z. B. Anrede) wird abgelehnt", False)
    except ValueError:
        ok("Unbekannter Weg (z. B. Anrede) wird abgelehnt", True)

    # --- Schalter der Person ---------------------------------------------------------------------------------------
    ok(f"Standard: alle Quellen an ({ai_ctx.prefs('kr-a')})", ai_ctx.prefs("kr-a") == {s: True for s in ai_ctx.SOURCES})
    for src, way, probe in (("pages", "ask", lambda cx: not ids_of(cx) and not cx.text),
                            ("tasks", "ask", lambda cx: "tasks:open" not in cx.read and CAN["task"] not in cx.extra),
                            ("calendar", "ask", lambda cx: not any(x.startswith("cal:") for x in cx.read) and "Calendar" not in cx.extra),
                            ("chat", "chat", lambda cx: not cx.history and cx.read == [])):
        ai_ctx.set_prefs("kr-a", {src: False})
        cx = ai_ctx.select("kr-a", Q, way=way, budget=8000, want=all_on, chat_id=7, history=hist)
        ok(f"Personen-Schalter {src} aus: Quelle wirkt nicht, obwohl die Anfrage sie will ({cx.read})", probe(cx), cx.read)
        ok(f"Personen-Schalter {src} aus: nicht in sources ({cx.sources})", src not in cx.sources)
        ai_ctx.set_prefs("kr-a", {src: True})
    with connect() as c:
        ok("Alle wieder an: kein user_kv-Eintrag (Standard)", c.execute("SELECT 1 FROM user_kv WHERE uid='kr-a' AND k='ai_sources'").fetchone() is None)
    for bad in ({"foo": True}, {"tasks": "nein"}, ["tasks"], {"pages": 0}):
        try:
            ai_ctx.set_prefs("kr-a", bad)
            ok(f"set_prefs lehnt {bad!r} ab", False)
        except ValueError:
            ok(f"set_prefs lehnt {bad!r} ab", True)
    try:
        ai_ctx.record(None, job_id=1, ctx=ai_ctx.Ctx())
        ok("record(job_id) meldet: kommt mit K-A2", False)
    except NotImplementedError:
        ok("record(job_id) meldet: kommt mit K-A2", True)

    # --- API: Personen-Schalter ------------------------------------------------------------------------------------
    s, d = K.req("GET", "/api/ai/status", A)
    ok(f"Status nennt sources ({d.get('sources')})", s == 200 and d.get("sources") == {x: True for x in ai_ctx.SOURCES})
    K.req("PUT", "/api/ai/settings", A, {"history": True})
    s, d = K.req("PUT", "/api/ai/settings", A, {"sources": {"tasks": False}})
    ok(f"PUT sources: Antwort mit neuen Schaltern, Verlauf unberuehrt ({s}, {d})", s == 200 and d == {"sources": dict(
        {x: True for x in ai_ctx.SOURCES}, tasks=False)} and K.req("GET", "/api/ai/status", A)[1].get("history") is True)
    s, d = K.req("PUT", "/api/ai/settings", A, {"sources": {"tasks": "aus"}})
    ok(f"PUT sources falsche Form -> 400 ({s})", s == 400)
    s, d = K.req("PUT", "/api/ai/settings", A, {"sources": {"mail": False}})
    ok(f"PUT sources unbekannte Quelle -> 400 ({s})", s == 400)
    ok("... Schalter unveraendert", ai_ctx.prefs("kr-a")["tasks"] is False and ai_ctx.prefs("kr-a")["pages"] is True)

    # --- Ueber die Endpunkte: done.read, Verlauf, Modell-Nachricht ----------------------------------------------------
    K.fake("POST", "/_reset")
    st, ev, _ = K.ask(A, Q, tasks=True, calendar=True)
    dn = K.get(ev, "done") or {}
    sysm, user, _, _ = K.last()
    ok(f"Frage: tasks per Person aus -> nicht in done.read ({dn.get('read')})", st == 200 and "tasks:open" not in (dn.get("read") or [])
       and any(x.startswith("cal:") for x in dn.get("read") or []) and CAN["task"] not in user)
    ok(f"Frage: done.read = Quellen der Antwort ({dn.get('read')})", sorted(x for x in dn.get("read") or [] if x.startswith("note:")) == sorted(
        f"note:{x['id']}" for x in (K.get(ev, "sources") or {}).get("sources") or []) and all(READ_RE.match(x) for x in dn["read"]))
    leak = [k for k in NEVER if CAN[k] in user]
    ok(f"Frage: Modell bekommt keine Canary aus Papierkorb/Archiv/Vorlage/Bert-privat/foederiert ({leak})", not leak and CAN["a_own"] in user
       and CAN["b_shared"] in user)
    with connect() as c:
        r = c.execute("SELECT id, sources FROM ai_history WHERE uid='kr-a' ORDER BY id DESC LIMIT 1").fetchone()
    meta = json.loads(r[1]) if r else {}
    ok(f"Verlauf an: read im Eintrag gespeichert, gleich wie done ({meta.get('read')})", r is not None and r[0] == dn.get("history_id")
       and meta.get("read") == dn.get("read"))
    ai_ctx.set_prefs("kr-a", {"tasks": True})
    K.req("PUT", "/api/ai/settings", A, {"history": False})
    st, ev, _ = K.ask(A, Q)
    dn = K.get(ev, "done") or {}
    with connect() as c:
        n = c.execute("SELECT count(*) FROM ai_history WHERE uid='kr-a'").fetchone()[0]
    ok(f"Verlauf aus: done hat read, gespeichert wird nichts ({dn.get('read')}, {n})", dn.get("read") and "history_id" not in dn and n == 0)
    ai_ctx.set_prefs("kr-a", {"pages": False})
    st, ev, _ = K.ask(A, Q)
    dn = K.get(ev, "done") or {}
    ok(f"Seiten per Person aus: keine Quellen, kein Modellaufruf, read leer ({dn})", dn.get("no_match") is True and dn.get("read") == []
       and (K.get(ev, "sources") or {}).get("sources") == [])
    ai_ctx.set_prefs("kr-a", {"pages": True})
    st, ev, _ = K.sse(f"/api/ai/page/{P['b_shared']}", A, {"action": "summarize"})
    dn = K.get(ev, "done") or {}
    ok(f"Seiten-Hilfe: read = genau die Seite ({dn.get('read')})", st == 200 and dn.get("read") == [f"note:{P['b_shared']}"])
    st, d, _ = K.sse(f"/api/ai/page/{P['fed']}", A, {"action": "summarize"})
    ok(f"Seiten-Hilfe: foederierter Eigentuemer -> 404 ({st})", st == 404)
    st, d, _ = K.sse(f"/api/ai/page/{P['b_sh_arch']}", A, {"action": "summarize"})
    ok(f"Seiten-Hilfe: archivierte Unterseite einer geteilten Seite nicht lesbar ({st})", st in (403, 404))

    # Chat-Zuruf: Verlauf nur mit Personen-Schalter chat
    Ca, Cb = CT.Person("kr-a"), CT.Person("kr-b")
    dm = CT.req("POST", "/api/chats", Ca.k, {"kind": "dm", "user": "kr-b"})[1]["chat"]["id"]
    CT.req("PUT", f"/api/chats/{dm}/ai", Cb.k, {"allowed": True})
    st, ev, _ = K.sse(f"/api/chats/{dm}/ai", Ca.k, {"q": "@Marin was meint Bert?", "history": hist})
    dn = K.get(ev, "done") or {}
    user = K.last()[1]
    ok(f"Chat: read = chat:<id>:1, Verlauf im Prompt, keine Seiten ({dn.get('read')})", dn.get("read") == [f"chat:{dm}:1"]
       and CAN["chat"] in user and CAN["a_own"] not in user and "Verlauf: 1 Nachrichten" in (dn.get("text") or ""))
    ai_ctx.set_prefs("kr-a", {"chat": False})
    st, ev, _ = K.sse(f"/api/chats/{dm}/ai", Ca.k, {"q": "@Marin was meint Bert?", "history": hist})
    dn = K.get(ev, "done") or {}
    user = K.last()[1]
    ok(f"Chat: Personen-Schalter chat aus -> kein Verlauf an das Modell ({dn.get('read')})", st == 200 and dn.get("read") == []
       and CAN["chat"] not in user and "Verlauf: 0 Nachrichten" in (dn.get("text") or ""))
    ai_ctx.set_prefs("kr-a", {"chat": True})

    # --- read ohne Text: Canary-Woerter nirgends ausser in den Seiten (DB) und nie im Log --------------------------------
    hits = {}
    with connect() as c:
        cols = c.execute("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema='public' "
                         "AND data_type IN ('text', 'character varying', 'jsonb', 'json')").fetchall()
        for tb, col in cols:
            if tb.startswith("note") or tb == "task":   # dort stehen die Inhalte selbst (Seite, Versionen, Suchtext, Aufgabe)
                continue
            for k, w in CAN.items():
                n = c.execute(f'SELECT count(*) FROM "{tb}" WHERE "{col}"::text LIKE ?', (f"%{w}%",)).fetchone()[0]
                if n:
                    hits.setdefault(f"{tb}.{col}", []).append(k)
    ok(f"DB: kein Canary-Wort ausserhalb der Seiten/Aufgaben (auch nicht in ai_history/user_kv) ({hits})", not hits, hits)
    lp = os.environ.get("SAGA_SERVER_LOG")
    if lp and os.path.exists(lp):
        with open(lp, encoding="utf-8", errors="replace") as f:
            log = f.read()
        found = [k for k, w in CAN.items() if re.search(re.escape(w), log, re.I)]
        ok(f"Log: kein Canary-Wort ({found})", not found, found)
    else:
        print("HINWEIS: SAGA_SERVER_LOG fehlt - Log-Pruefung uebersprungen", flush=True)


if __name__ == "__main__":
    main()
