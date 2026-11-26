"""Kollegin: Einheiten und Spuren (Saga 2.6 K-A5, Bugs-DB 2266, Konzept K21, M8, RT-28) - saga/ai_units.py.

- Schaetzen/Abrechnen: estimate = ceil(Zeichen/4) + max_tokens belegt den Pool waehrend der Anfrage, settle rechnet
  ceil(Eingabe/4) + ceil(Ausgabe/4) ab (Rest zurueck), ohne Antwort (fake-500, fake-empty) 0; settle zaehlt einmal.
- Feste Aufteilung 60/30/10 des Tagesbudgets (Kontingent x 4000), Kontingent 0 = unbegrenzt (nur gemessen).
- Uebergang ohne Bruch (Abweichung c): /api/ai/status alte Felder in alter Reihenfolge byteweise gleich (units nur
  angehaengt), quota_left in 'done'/'error' wie bisher, keine neuen Felder in 'done', Frage-Pool lehnt nie ab.
- Pools job/idea: voll -> reserve None (zurueckstellen), laufender Auftrag ueberzieht genau einen Schritt;
  ai.stream_metered ohne Budget -> NoBudget ohne Modellaufruf.
- Admin (M8): weniger als 5 aktive Konten nur Summen, ab 5 je Person nur Ampel; Admin-Seite zeigt den Messwert klein.
- Keine Inhalte in ai_units (nur Zahlen-Spalten, Canary-Woerter nie), Kontoloeschung raeumt ab.
- Echte Tokens: fake-usage (OpenAI stream_options.include_usage, Ollama prompt_eval_count/eval_count) -> real_in/real_out;
  fake-nousage (400 auf stream_options) -> einmal ohne wiederholen, danach gemerkt.
- Spuren (RT-28): 50 Hintergrund-Anfragen -> hoechstens 1 Platz, Fragen bekommen trotzdem sofort alle PARALLEL Plaetze;
  ai._gate laeuft ueber dieselbe Buchhaltung (eine Anfrage je Person, PARALLEL gesamt); 1-MB-Seite aufs Budget gekuerzt.
Tageslimit waehrend eines Auftrags ("zurueckgestellt, nicht verworfen") prueft tests/kollegin_fehler.py (K-A6).
- K-A6 Runner ai.task: Schritt in der Hintergrund-Spur ("job:<id>"), belegt -> zurueckgestellt 'spur' ohne Belegung,
  danach weiter; waehrend des Schritts bekommt eine Frage trotzdem einen Platz; Pool job = Abrechnung (Zeichen/4),
  Spuren danach leer; AI_MAX_PARALLEL unveraendert (PARALLEL wie saga.ai).

Aufruf (saga-test): tools/testumgebung/umgebung.sh lauf server <slot> kollegin_budget.py
Wegwerf-Konten kb-*; raeumt auf."""
import asyncio, http.client, json, math, os, socket, subprocess, sys, threading, time
import auth
from db import connect

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ki as K  # noqa: E402

from saga import ai, ai_units as U  # noqa: E402

CANARY = "KANARIEbudgetKx1"
OLD_STATUS = ["enabled", "host", "model", "provider", "quota", "used", "quota_left", "history_allowed", "history", "notice",
              "targets", "q_max", "blocks_max", "persona", "chat", "web", "caps", "sources"]      # Stand vor K-A5 (57f59a3)


def ok(n, c, info=""):
    K.ok(n if c else f"{n} {str(info)[:600]}", c)


def units(uid, pool="ask"):
    with connect() as c:
        r = c.execute("SELECT units, real_in, real_out FROM ai_units WHERE uid=? AND day=? AND pool=?", (uid, U.today(), pool)).fetchone()
    return tuple(int(x) for x in r) if r else (0, 0, 0)


def msgs_chars():
    return U.chars_of(K.lastbody().get("messages") or [])


def raw_status(cookie):
    c = http.client.HTTPConnection(K.B_.hostname, K.B_.port, timeout=30)
    c.request("GET", "/api/ai/status", headers={"Host": K.HOST, "Accept-Language": "de", "Cookie": "saga=" + cookie, "X-Saga": "1"})
    r = c.getresponse()
    data = r.read().decode("utf-8")
    c.close()
    return r.status, data


def setai(**kw):
    cur = dict(auth.settings(fresh=True)["ai"])
    cur.update(kw)
    auth.save_setting("ai", cur)
    time.sleep(K.WAIT)


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'kb-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        for t in ("note", "note_version", "user_kv", "task", "ai_history", "ai_usage", "ai_units", "notification"):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE 'kb-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'kb-%'")


def main():
    cleanup()
    prev = auth.settings(fresh=True)
    pv = dict(ai=dict(prev["ai"]), share=prev.get("share_enabled", True))
    auth.save_setting("share_enabled", True)
    auth.save_setting("ai", {"enabled": True, "provider": "openai", "url": K.FAKE + "/v1", "model": "fake", "api_key": "", "groups": [],
                             "timeout": 10, "quota": 3, "context": 8000, "history": True, "pinned": ["127.0.0.1"], "chat": False,
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
        auth.save_setting("ai", pv["ai"])
        K.key_raw(pv["ai"].get("api_key") or "")
        auth.save_setting("share_enabled", pv["share"])
        cleanup()
    print(f"\nKollegin Budget (K-A5) {sum(K.res)}/{len(K.res)}", "- ALLES GRUEN" if all(K.res) else "- FEHLER")
    sys.exit(0 if all(K.res) else 1)


def run():
    for u in "abcdef":
        auth.create_user(f"kb-{u}", K.PW, f"Person {u.upper()} KB")
    auth.create_user("kb-adm", K.PW, "Admin KB", is_admin=True)
    A, Bk, Ck = K.ck("kb-a"), K.ck("kb-b"), K.ck("kb-c")
    adm = K.Admin("kb-adm")
    time.sleep(K.WAIT)
    K.page(A, "Zitronenfalter Budget", f"Das Budget fuer Zitronenfalter betraegt 4711 Euro. {CANARY}")
    K.page(Bk, "Zitronenfalter bei Bert", f"Zitronenfalter sind gelb. {CANARY}")
    K.page(Ck, "Zitronenfalter bei Cora", "Zitronenfalter fliegen im Fruehling.")

    # --- Rechnen (im Prozess) ----------------------------------------------------------------------------------------
    m = [{"role": "system", "content": "x" * 10}, {"role": "user", "content": "y" * 7}]
    ok(f"estimate = ceil(Zeichen/4) + max_tokens ({U.estimate(m, 100)})", U.estimate(m, 100) == math.ceil(17 / 4) + 100 == 105)
    ok("cost: ceil(Eingabe/4) + ceil(Ausgabe/4), ohne Antwort 0", U.cost(17, 9) == 5 + 3 and U.cost(17, 0) == 0 and U.cost(0, 1) == 1)
    cfg50 = {"quota": 50}
    b = {p: U.budget("kb-a", p, cfg50) for p in U.POOLS}
    ok(f"Aufteilung 60/30/10 von 50 x 4000 ({b})", b == {"ask": 120000, "job": 60000, "idea": 20000} and sum(b.values()) == 50 * U.PER_REQUEST)
    ok("Kontingent 0 = unbegrenzt (nur gemessen)", all(U.budget("kb-a", p, {"quota": 0}) is None for p in U.POOLS))
    ok("SPLIT dokumentiert 0.6/0.3/0.1", U.SPLIT == {"ask": .6, "job": .3, "idea": .1})

    u0 = units("kb-f", "job")[0]
    r = U.reserve("kb-f", "job", 400, cfg={"quota": 0})
    ok(f"reserve belegt die Schaetzung ({units('kb-f', 'job')})", r is not None and units("kb-f", "job")[0] == u0 + 400)
    got = U.settle(r, 40, 21)
    ok(f"settle: Rest zurueck, abgerechnet 10 + 6 ({got}, {units('kb-f', 'job')})", got == 16 and units("kb-f", "job")[0] == u0 + 16)
    U.settle(r, 4000, 4000)
    ok("settle zaehlt einmal", units("kb-f", "job")[0] == u0 + 16)
    r = U.reserve("kb-f", "job", 300, cfg={"quota": 0})
    ok("ohne Antwort: 0 Einheiten (cancel/settle mit 0 Zeichen Ausgabe)", U.cancel(r) == 0 and units("kb-f", "job")[0] == u0 + 16)

    # --- Pools job/idea: durchgesetzt; ask nur gemessen ---------------------------------------------------------------
    q1 = {"quota": 1}                                    # Tagesbudget 4000: ask 2400, job 1200, idea 400
    with connect() as c, c:
        c.execute("DELETE FROM ai_units WHERE uid='kb-e'")
    r1 = U.reserve("kb-e", "job", 1000, cfg=q1)
    r2 = U.reserve("kb-e", "job", 300, cfg=q1)
    ok(f"Job-Pool: 1000 passt, weitere 300 nicht -> None (zurueckstellen) ({units('kb-e', 'job')})",
       r1 is not None and r2 is None and units("kb-e", "job")[0] == 1000)
    r3 = U.reserve("kb-e", "job", 300, cfg=q1, job_running=True)
    r4 = U.reserve("kb-e", "job", 1, cfg=q1, job_running=True)
    ok(f"laufender Auftrag ueberzieht genau einen Schritt (1300 > 1200, danach None) ({units('kb-e', 'job')})",
       r3 is not None and r4 is None and units("kb-e", "job")[0] == 1300)
    ok("Ideen-Pool: 401 > 400 -> None, 400 passt", U.reserve("kb-e", "idea", 401, cfg=q1) is None and U.reserve("kb-e", "idea", 400, cfg=q1))
    ok("Frage-Pool lehnt nie ab (Abweichung c)", U.reserve("kb-e", "ask", 10 ** 7, cfg=q1) is not None)
    K.fake("POST", "/_reset")
    c_ai = dict(auth.settings(fresh=True)["ai"], quota=1)
    try:
        ai.stream_metered("kb-e", "job", c_ai, [{"role": "user", "content": "Reply with the single word: OK"}], lambda s: None, max_tokens=64)
        nb = False
    except U.NoBudget:
        nb = True
    ok(f"stream_metered ohne Budget im Job-Pool -> NoBudget, kein Modellaufruf ({K.fake('GET', '/_count')})", nb and K.fake("GET", "/_count")["n"] == 0)
    with connect() as c, c:
        c.execute("DELETE FROM ai_units WHERE uid='kb-e'")
    got = []
    mm = [{"role": "user", "content": "Reply with the single word: OK"}]
    ai.stream_metered("kb-e", "job", c_ai, mm, got.append, max_tokens=64)
    ok(f"stream_metered im Job-Pool: Modell gerufen, abgerechnet ({''.join(got)!r}, {units('kb-e', 'job')})",
       "".join(got) == "OK" and units("kb-e", "job")[0] == U.cost(U.chars_of(mm), 2))

    # --- Frage-Spur: alte Felder unveraendert, Einheiten gemessen ------------------------------------------------------
    s, raw = raw_status(A)
    d = json.loads(raw)
    keys = list(d)
    old = {k: d[k] for k in OLD_STATUS if k in d}
    # spaetere Felder (z. B. style, 2268) duerfen nach units angehaengt werden - die alten bleiben vorn und byteweise gleich
    ok(f"status: alte Felder in alter Reihenfolge, units nur angehaengt ({keys})", s == 200 and keys[:len(OLD_STATUS) + 1] == OLD_STATUS + ["units"])
    ok("status: alte Felder byteweise gleich (Text bis vor units = alte Darstellung)",
       raw.startswith(json.dumps(old, ensure_ascii=False, separators=(",", ":"))[:-1] + ',"units":'
                      + json.dumps(d["units"], ensure_ascii=False, separators=(",", ":"))))
    ok(f"status: quota 3, used 0, quota_left 3, units {{left, of}} = 3 x 4000 ({d['quota']}, {d['used']}, {d['quota_left']}, {d['units']})",
       d["quota"] == 3 and d["used"] == 0 and d["quota_left"] == 3 and d["units"] == {"left": 12000, "of": 12000})

    u0 = units("kb-a")[0]
    s, ev, _ = K.ask(A, "Was weiss ich ueber Zitronenfalter?")
    dn, txt, ch = K.get(ev, "done") or {}, K.text_of(ev), msgs_chars()
    ok(f"Frage: done.quota_left 2, keine neuen Felder in done ({sorted(dn)})", s == 200 and dn.get("quota_left") == 2 and sorted(dn) == ["quota_left", "read"])
    ok(f"Frage gemessen: Einheiten = ceil(Eingabe/4) + ceil(Ausgabe/4) ({units('kb-a')[0] - u0}, {ch}, {len(txt)})",
       txt and units("kb-a")[0] - u0 == U.cost(ch, len(txt)))
    ok(f"ai_usage zaehlt wie bisher ({K.used('kb-a')})", K.used("kb-a") == 1)
    st = K.req("GET", "/api/ai/status", A)[1]
    ok(f"status nach einer Frage: used 1, quota_left 2, units.left = of - Verbrauch ({st['units']})",
       st["used"] == 1 and st["quota_left"] == 2 and st["units"]["left"] == 12000 - units("kb-a")[0])

    setai(model="fake-500")
    u1 = units("kb-a")[0]
    s, ev, _ = K.ask(A, "Was weiss ich ueber Zitronenfalter?")
    er = K.get(ev, "error") or {}
    ok(f"Fehler ohne Antwort: quota_left unveraendert 2, Anfrage zurueckgebucht, 0 Einheiten ({er}, {units('kb-a')[0] - u1})",
       er.get("quota_left") == 2 and er.get("partial") is False and K.used("kb-a") == 1 and units("kb-a")[0] == u1)
    setai(model="fake-empty")
    s, ev, _ = K.ask(A, "Was weiss ich ueber Zitronenfalter?")
    ok(f"leere Antwort (fake-empty): 0 Einheiten, quota_left 2 ({K.get(ev, 'error')})",
       (K.get(ev, "error") or {}).get("quota_left") == 2 and units("kb-a")[0] == u1)

    # --- Schaetzung waehrend der Anfrage (fake-hang: erstes Stueck, dann 8 s Pause) + eine Anfrage je Person ----------------
    setai(model="fake-hang")
    u2 = units("kb-a")[0]
    out = {}
    th = threading.Thread(target=lambda: out.update(r=K.ask(A, "Was weiss ich ueber Zitronenfalter?")))
    K.fake("POST", "/_reset")
    th.start()
    for _ in range(60):
        if K.fake("GET", "/_count")["n"]:
            break
        time.sleep(0.1)
    time.sleep(0.5)
    ch = msgs_chars()
    mid = units("kb-a")[0] - u2
    ok(f"waehrend der Anfrage: Pool mit estimate belegt (ceil(Zeichen/4) + {ai.MAX_TOKENS}) ({mid}, {ch})",
       mid == math.ceil(ch / 4) + ai.MAX_TOKENS)
    s2 = K.ask(A, "Noch eine Frage zu Zitronenfaltern?")[0]
    s3, ev3, _ = K.ask(Bk, "Was weiss ich ueber Zitronenfalter?")
    ok(f"_gate ueber ai_units: zweite Anfrage derselben Person 409, andere Person sofort dran ({s2}, {s3})", s2 == 409 and s3 == 200)
    th.join(30)
    r = out.get("r") or (0, [], {})
    txt = K.text_of(r[1])
    ok(f"nachher abgerechnet, Rest zurueck ({units('kb-a')[0] - u2}, {U.cost(ch, len(txt))})",
       r[0] == 200 and txt and units("kb-a")[0] - u2 == U.cost(ch, len(txt)) < mid)
    ok("Spur danach wieder frei", K.ask(A, "Und noch einmal Zitronenfalter?")[0] == 200)

    # --- Pruefer: kein Platz-Leck, wenn der Client mitten im Strom abbricht oder das Modell scheitert -------------------
    setai(quota=0)                                       # kb-a hat das Kontingent 3 hier schon verbraucht
    u4 = units("kb-a")[0]
    K.fake("POST", "/_reset")
    s, seen = abort_after_delta(A, "Was weiss ich ueber Zitronenfalter?")
    est_ab = math.ceil(msgs_chars() / 4) + ai.MAX_TOKENS
    t0, st2, ev2 = time.monotonic(), None, []
    while time.monotonic() - t0 < 20:                    # fake-hang: naechstes Stueck erst nach 8 s
        st2, ev2, _ = K.ask(A, "Zitronenfalter nach dem Abbruch?")
        if st2 != 409:
            break
        time.sleep(0.5)
    dt = time.monotonic() - t0
    ok(f"Abbruch durch den Client: Platz wieder frei ({s}, Stueck gesehen {seen}, danach {st2} nach {dt:.1f} s)",
       s == 200 and seen and st2 == 200, (s, seen, st2, dt))
    cost2 = U.cost(msgs_chars(), len(K.text_of(ev2)))
    for _ in range(40):                                  # Abrechnung im Modell-Thread (nach dem naechsten Stueck)
        if units("kb-a")[0] - u4 - cost2 < est_ab:
            break
        time.sleep(0.5)
    ok(f"Abbruch: Schaetzung abgerechnet, nicht stehen geblieben ({units('kb-a')[0] - u4 - cost2} < {est_ab})",
       0 < units("kb-a")[0] - u4 - cost2 < est_ab)
    setai(model="fake-500")
    s, ev, _ = K.ask(A, "Zitronenfalter mit Modellfehler?")
    setai(model="fake")
    s2 = K.ask(A, "Zitronenfalter nach dem Modellfehler?")[0]
    ok(f"Modellfehler: Platz sofort wieder frei ({s}, {K.get(ev, 'error') is not None}, danach {s2})",
       s == 200 and K.get(ev, "error") is not None and s2 == 200)

    # --- Echte Tokens: fake-usage / fake-nousage ------------------------------------------------------------------------
    setai(model="fake-usage", quota=0)
    _, ri0, ro0 = units("kb-b")
    s, ev, _ = K.ask(Bk, "Was weiss ich ueber Zitronenfalter?")
    bd = K.lastbody()
    n_in = sum(len(str(x.get("content") or "").split()) for x in bd.get("messages") or []) + 7
    n_out = len(K.text_of(ev).split()) + 3
    _, ri, ro = units("kb-b")
    ok(f"OpenAI: stream_options.include_usage gesendet, real_in/real_out aus usage ({ri - ri0}/{ro - ro0} = {n_in}/{n_out})",
       bd.get("stream_options") == {"include_usage": True} and (ri - ri0, ro - ro0) == (n_in, n_out))
    ok("quota 0: quota_left None in done (wie bisher)", (K.get(ev, "done") or {}).get("quota_left", "x") is None)
    setai(model="fake-nousage")
    K.fake("POST", "/_reset")
    ub, ri0, ro0 = units("kb-b")
    s, ev, _ = K.ask(Bk, "Was weiss ich ueber Zitronenfalter?")
    n1 = K.fake("GET", "/_count")["n"]
    ok(f"Rueckfall: 400 auf stream_options -> einmal ohne wiederholt, Antwort da, nur Zeichen gezaehlt ({n1}, {units('kb-b')})",
       s == 200 and n1 == 2 and K.text_of(ev).startswith("Antwort aus") and units("kb-b")[1:] == (ri0, ro0) and units("kb-b")[0] > ub
       and "stream_options" not in K.lastbody())
    s, ev, _ = K.ask(Bk, "Was weiss ich ueber Zitronenfalter?")
    ok(f"Rueckfall gemerkt (_NO_USAGE): naechste Anfrage ohne stream_options, ein Aufruf ({K.fake('GET', '/_count')})",
       s == 200 and K.fake("GET", "/_count")["n"] == 3 and "stream_options" not in K.lastbody())
    setai(provider="ollama", url=K.FAKE, model="fake-usage")
    _, ri0, ro0 = units("kb-c")
    s, ev, _ = K.ask(Ck, "Was weiss ich ueber Zitronenfalter?")
    bd = K.lastbody()
    n_in = sum(len(str(x.get("content") or "").split()) for x in bd.get("messages") or []) + 7
    n_out = len(K.text_of(ev).split()) + 3
    _, ri, ro = units("kb-c")
    ok(f"Ollama: prompt_eval_count/eval_count -> real_in/real_out, kein stream_options ({ri - ri0}/{ro - ro0})",
       s == 200 and (ri - ri0, ro - ro0) == (n_in, n_out) and "stream_options" not in bd)

    # --- RT-28: 1-MB-Seite aufs Budget gekuerzt ---------------------------------------------------------------------------
    setai(provider="openai", url=K.FAKE + "/v1", model="fake", context=8000)
    big = K.page(Ck, "Riesenseite Zitronenfalter", "Platz")
    with connect() as c, c:
        c.execute("UPDATE note SET body=? WHERE id=?", (K.blocks(*[f"Zitronenfalter Absatz {i}. " + "Text " * 200 for i in range(1000)]), big))
    u3 = units("kb-c")[0]
    s, ev, _ = sse_page(Ck, big)
    ch = msgs_chars()
    ok(f"RT-28: 1-MB-Seite zusammenfassen -> Kontext aufs Budget gekuerzt ({ch} Zeichen), Einheiten danach ({units('kb-c')[0] - u3})",
       s == 200 and (K.get(ev, "done") or {}).get("truncated") is True and ch < 8000 + 4000
       and 0 < units("kb-c")[0] - u3 <= math.ceil((8000 + 4000) / 4) + math.ceil(len(K.text_of(ev)) / 4))
    s, ev, _ = K.ask(Ck, "Was steht auf der Riesenseite Zitronenfalter?")
    ok(f"RT-28: Frage mit 1-MB-Seite -> Kontext aufs Budget gekuerzt ({msgs_chars()})", s == 200 and msgs_chars() < 8000 + 4000)

    # --- Spuren (im Prozess, RT-28) ------------------------------------------------------------------------------------------
    for v in U.LANES.values():
        v.clear()
    P = 4
    wins, lock = [], threading.Lock()
    gate = threading.Barrier(50)

    def bg(i):
        gate.wait()
        if U.lane_take("bg", f"job:{i}", parallel=P):
            with lock:
                wins.append(i)
    ths = [threading.Thread(target=bg, args=(i,)) for i in range(50)]
    [x.start() for x in ths]
    [x.join() for x in ths]
    ok(f"RT-28: 50 Hintergrund-Anfragen gleichzeitig -> genau 1 Platz ({len(wins)})", len(wins) == 1)
    t0 = time.monotonic()
    asks = [U.lane_try("ask", f"kb-{u}", parallel=P) for u in "abcd"]
    dt = time.monotonic() - t0
    ok(f"RT-28: Fragen trotz Hintergrund sofort, alle {P} Plaetze ({asks}, {dt * 1000:.1f} ms)", asks == [None] * 4 and dt < 0.05)
    ok("Fragen-Grenze bleibt PARALLEL (5. Frage -> full), eine je Person (busy)",
       U.lane_try("ask", "kb-e", parallel=P) == "full" and U.lane_try("ask", "kb-a", parallel=P) == "busy")
    U.lane_free("bg", f"job:{wins[0]}")
    ok("Hintergrund nur, wenn weniger als PARALLEL-1 Fragen laufen",
       U.lane_try("bg", "job:x", parallel=P) == "full")
    U.lane_free("ask", "kb-a")
    U.lane_free("ask", "kb-b")
    ok("... bei 2 von 4 Fragen wieder ein Hintergrund-Platz, ein zweiter nicht",
       U.lane_take("bg", "job:x", parallel=P) and not U.lane_take("bg", "job:y", parallel=P))
    for v in U.LANES.values():
        v.clear()
    ok("PARALLEL 2: Hintergrund nur ohne laufende Frage, Fragen trotzdem 2",
       U.lane_take("bg", "j", parallel=2) and U.lane_take("ask", "a", parallel=2) and U.lane_take("ask", "b", parallel=2)
       and not U.lane_take("ask", "c", parallel=2))
    for v in U.LANES.values():
        v.clear()
    ok("PARALLEL 1: Hintergrund belegt den einen Platz (Frage -> full), sonst Frage zuerst",
       U.lane_take("bg", "j", parallel=1) and U.lane_try("ask", "a", parallel=1) == "full" and not U.lane_free("bg", "j")
       and U.lane_take("ask", "a", parallel=1) and not U.lane_take("bg", "j", parallel=1))
    for v in U.LANES.values():
        v.clear()
    # Dauerlast: viele Threads nehmen/geben -> nie mehr als 1 Hintergrund gleichzeitig
    peak, cur, stop = [0], [0], time.monotonic() + 1.5

    def churn(i):
        while time.monotonic() < stop:
            if U.lane_take("bg", f"j{i}", parallel=P):
                with lock:
                    cur[0] += 1
                    peak[0] = max(peak[0], cur[0])
                time.sleep(0.001)
                with lock:
                    cur[0] -= 1
                U.lane_free("bg", f"j{i}")
    ths = [threading.Thread(target=churn, args=(i,)) for i in range(50)]
    [x.start() for x in ths]
    [x.join() for x in ths]
    ok(f"RT-28 Dauerlast: nie mehr als 1 Hintergrund gleichzeitig (Spitze {peak[0]})", peak[0] == 1)
    # _gate im Prozess ueber dieselbe Buchhaltung
    for v in U.LANES.values():
        v.clear()
    U.lane_take("bg", "job:1", parallel=ai.PARALLEL)

    async def gates():
        out = []
        for u in ["kb-a", "kb-a"] + [f"kb-{x}" for x in "bcdef"][:ai.PARALLEL]:
            c, err = await ai._gate(u)
            out.append(err.status_code if err is not None else 200)
        return out
    gs = asyncio.run(gates())
    want = [200, 409] + [200] * (ai.PARALLEL - 1) + [503]
    ok(f"ai._gate: Hintergrund belegt, Fragen wie bisher (je Person 1 -> 409, PARALLEL gesamt -> 503) ({gs})", gs == want)
    for v in U.LANES.values():
        v.clear()

    # --- Admin: < 5 Konten Summen, ab 5 Ampel ------------------------------------------------------------------------------
    with connect() as c, c:
        c.execute("DELETE FROM ai_units WHERE uid='kb-d'")
    q1 = {"quota": 1}                                    # 4000 Einheiten je Person
    with connect() as c, c:
        # begrenzte Pools bei Kontingent 1: Auftraege 1200 + Ideen 400 = 1600 -> kb-d 1300 (81 %) gelb, kb-e 1700 rot
        c.execute("INSERT INTO ai_units (uid, day, pool, units, real_in, real_out) VALUES ('kb-d', ?, 'job', 1300, 0, 0) "
                  "ON CONFLICT (uid, day, pool) DO UPDATE SET units=1300", (U.today(),))
        c.execute("UPDATE ai_units SET units=1700 WHERE uid='kb-e' AND day=? AND pool='job'", (U.today(),))
    v4 = U.admin_view(q1, accounts=4)
    ok(f"Admin < 5 Konten: nur Summen, keine Personen ({sorted(v4)}, lights={v4['lights']})",
       v4["lights"] is None and set(v4["units"]) == set(U.POOLS) and v4["total"] == sum(v4["units"].values()) and v4["people"] >= 5)
    v5 = U.admin_view(q1, accounts=5)
    lv = v5["lights"] or {}
    ok(f"Admin ab 5 Konten: nur Anzahlen je Farbe, keine Kennung ({lv})",
       set(lv) == {"gruen", "gelb", "rot"} and all(isinstance(n, int) for n in lv.values())
       and lv["gelb"] >= 1 and lv["rot"] >= 1 and "kb-" not in json.dumps(v5))
    with connect() as c, c:                              # nur Fragen (nur gemessen) machen nie gelb/rot
        c.execute("INSERT INTO ai_units (uid, day, pool, units, real_in, real_out) VALUES ('kb-f', ?, 'ask', 999999, 0, 0) "
                  "ON CONFLICT (uid, day, pool) DO UPDATE SET units=999999", (U.today(),))
    v6 = U.admin_view(q1, accounts=5)
    ok(f"Admin-Ampel: nur Fragen zaehlen nicht (Anfragen/Tag begrenzt sie) ({v6['lights']})", v6["lights"] == lv)
    real = U.admin_view(q1)
    ok(f"Admin: Zahl aktiver Konten aus der Datenbank ({real['accounts']})", real["accounts"] >= 7
       and (real["lights"] is None) == (real["accounts"] < 5))
    setai(quota=1)                                       # Server rechnet die Ampel mit 4000 Einheiten je Person
    s, html = adm.go("/settings/ai")
    seg = html.split('id="ai-units"', 1)[-1].split("</p>", 1)[0]
    ok("Admin-Seite: Anfragen/Tag bleibt die Stellgroesse, Einheiten klein als Messwert",
       s == 200 and 'name="quota"' in html and 'id="ai-units"><small>Messwert heute in Einheiten' in html
       and "Fragen " in html and "Aufträge " in html)
    ok("Admin-Seite (>= 5 Konten): Ampel nur als Anzahlen je Farbe, keine Kennung, keine Einzelwerte",
       "je Person nur Ampel:" in seg and "× Gelb" in seg and "× Rot" in seg and "kb-" not in seg
       and "1300" not in seg and "1700" not in seg, seg)
    ok("Admin-Seite: echte Tokens als Summe", "echte Tokens laut Endpunkt:" in seg, seg)

    # --- K-A6: Runner ai.task in der Hintergrund-Spur -------------------------------------------------------------------
    from saga import actor, ai_task, bus, jobs, testclock
    setai(quota=0)
    from saga import ai_saga                                   # 2.6 K-I (2269): ohne Einwilligung haelt jeder Schritt an
    ai_saga.grant("kb-e")
    testclock.set_clock({"set": time.time() + 30 * 86400})     # Server-Worker der Testumgebung sieht den Auftrag nie faellig
    try:
        def mkjob(text):
            jid = jobs.create("kb-e", ai_task.KIND, steps=[{"tool": "create_task", "args": {"text": text}}])
            st = jobs.state_of(jid)
            jobs.approve(jid, "kb-e", st["plan_v"], st["plan_hash"])
            return jid

        def ntask(text):
            with connect() as c:
                return c.execute("SELECT COUNT(*) FROM task WHERE uid='kb-e' AND text=?", (text,)).fetchone()[0]
        ok("AI_MAX_PARALLEL unveraendert: Spuren rechnen mit saga.ai.PARALLEL", U.PARALLEL == ai.PARALLEL)
        jid = mkjob("KB Spur belegt")
        ok("Hintergrund-Platz von anderem belegt", U.lane_take("bg", "kb-anderer"))
        try:
            t0 = testclock.now()
            jobs.run_once()
            s = jobs.state_of(jid)
            with connect() as c:
                nres = c.execute("SELECT COUNT(*) FROM job_event WHERE job_id=? AND kind='units'", (jid,)).fetchone()[0]
            ok(f"Spur belegt: zurueckgestellt 'spur' (~{ai_task.LANE_WAIT} s), nichts geschrieben, nichts belegt "
               f"({s['state']}/{s['sub']}, {s['reason']}, {round(s['next_at'] - t0)})",
               s["state"] == "laeuft" and s["reason"] == "spur" and abs(s["next_at"] - t0 - ai_task.LANE_WAIT) < 2
               and ntask("KB Spur belegt") == 0 and nres == 0)
        finally:
            U.lane_free("bg", "kb-anderer")
        testclock.set_clock({"advance": ai_task.LANE_WAIT + 1})
        jobs.run_once()
        ok("Spur frei: Schritt laeuft, erledigt", jobs.state_of(jid)["state"] == "erledigt" and ntask("KB Spur belegt") == 1)
        # waehrend des Schritts: Hintergrund belegt genau einen Platz, eine Frage kommt trotzdem dran
        jid = mkjob("KB Spur Frage")
        seen = {}

        def hold(c, **d):
            a = actor.current()
            if a is not None and a.job_id == jid:
                seen["lanes"] = U.lanes()
                seen["ask"] = U.lane_try("ask", "kb-frage")
                U.lane_free("ask", "kb-frage")
        bus.on("task.created", hold)
        u0 = units("kb-e", "job")[0]
        try:
            jobs.run_once()
        finally:
            bus._hooks["task.created"].remove(hold)
        res = jobs.steps_of(jid)[0]["result"] or {}
        with connect() as c:
            ev = c.execute("SELECT data FROM job_event WHERE job_id=? AND kind='units'", (jid,)).fetchone()[0]
        cost = U.cost(res.get("chars", 0), len(json.dumps({"text": "KB Spur Frage"}, separators=(",", ":"))))
        ok(f"im Schritt: Hintergrund 1 Platz, Frage bekommt trotzdem einen ({seen})",
           seen.get("lanes", {}).get("bg") == 1 and seen.get("ask") is None)
        ok(f"Pool job = Abrechnung (Ergebnis + Angaben je Zeichen/4) ({units('kb-e', 'job')[0] - u0}, {ev}, {cost})",
           units("kb-e", "job")[0] - u0 == cost == ev.get("units") and ev.get("open") == 0
           and jobs.state_of(jid)["units"] == cost)
        ok(f"danach beide Spuren leer ({U.lanes()})", U.lanes() == {"ask": 0, "bg": 0})
    finally:
        testclock.set_clock({"reset": True})

    # --- Keine Inhalte in ai_units, Aufraeumen ---------------------------------------------------------------------------
    with connect() as c:
        cols = c.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name='ai_units' ORDER BY ordinal_position").fetchall()
        rows = c.execute("SELECT * FROM ai_units WHERE uid LIKE 'kb-%'").fetchall()
    ok(f"ai_units: nur Kennung, Tag, Pool und Zahlen ({cols})",
       [tuple(x) for x in cols] == [("uid", "text"), ("day", "text"), ("pool", "text"), ("units", "bigint"), ("real_in", "bigint"),
                                    ("real_out", "bigint")])
    flat = json.dumps([list(r) for r in rows])
    ok(f"ai_units: keine Inhalte (Canary, Fragen, Titel) ({len(rows)} Zeilen)", rows and CANARY not in flat and "Zitronenfalter" not in flat
       and all(r[2] in U.POOLS and isinstance(r[3], int) for r in rows))
    from saga.userdata import USER_TABLES, delete_user_data
    delete_user_data("kb-f")
    ok("Kontoloeschung raeumt ai_units ab (USER_TABLES)", "ai_units" in USER_TABLES and units("kb-f", "job") == (0, 0, 0))


def abort_after_delta(cookie, q):
    """Frage stellen, beim ersten delta die Verbindung hart schliessen -> (status, delta gesehen)."""
    c = http.client.HTTPConnection(K.B_.hostname, K.B_.port, timeout=30)
    c.request("POST", "/api/ai/ask", body=json.dumps({"q": q}),
              headers={"Host": K.HOST, "Accept-Language": "de", "Content-Type": "application/json", "Accept": "text/event-stream",
                       "Cookie": "saga=" + cookie, "X-Saga": "1"})
    r = c.getresponse()
    seen = False
    if r.status == 200:
        for _ in range(200):
            line = r.fp.readline()
            if not line:
                break
            if line.startswith(b"event: delta"):
                seen = True
                break
    sk = c.sock or getattr(getattr(r.fp, "raw", None), "_sock", None)
    try:
        sk.shutdown(socket.SHUT_RDWR)
    except (OSError, AttributeError):
        pass
    r.close()
    c.close()
    return r.status, seen


def sse_page(cookie, nid):
    return K.sse(f"/api/ai/page/{nid}", cookie, {"action": "summarize"})


if __name__ == "__main__":
    main()
