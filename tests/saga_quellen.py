"""Bugs-DB 2542: Quellen unter Antworten im Ort "Saga" - nur belegte ([n] in der Antwort), bei "nichts gefunden" keine.

Prueft (laufende Instanz unter HK_TEST_BASE + Fake-Modell tests/fakeai.py auf HK_FAKEAI_PORT, eigene DB):
  - ai_saga.cited_sources: [1], [1, 3], [2][9] (9 nicht gelesen), keine Marken -> leer
  - POST /api/ai/thread mit 5 gelesenen Seiten:
      fake        "Antwort aus 5 Quellen [1]."     -> letztes 'sources'-Ereignis vor 'done' = nur Quelle 1, gespeicherter Zug
                                                      ebenso; erstes 'sources'-Ereignis (fuer die [n]-Links beim Streamen)
                                                      und der Kontext-Ausweis read nennen weiter alle 5; done ohne 'sources'
      fake-nichts "... nichts gefunden."            -> letztes 'sources' leer, gespeicherter Zug ohne Quellen
      fake-demo   je Seite ein Satz mit [n]         -> alle 5
Aufruf: PYTHONPATH=tests/pgshim:. HK_FAKEAI_PORT=8955 python tests/saga_quellen.py
"""
import json
import os
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kollegin_einwilligung as K  # noqa: E402 - Helfer (req, sse, setai, Fake-Modell)

import auth  # noqa: E402

U = "kw-quellen"
res = []


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, "" if c else str(info)[:600], flush=True)


def evs(ev, typ):
    return [d for t_, d in ev if t_ == typ] if isinstance(ev, list) else []


def unit():
    from saga import ai_saga
    o = [{"n": i, "id": 100 + i, "title": f"S{i}"} for i in (1, 2, 3, 4, 5)]
    f = lambda t: [s["n"] for s in ai_saga.cited_sources(t, o)]      # noqa: E731
    ok("cited_sources: [1] -> 1", f("Steht so [1].") == [1], f("Steht so [1]."))
    ok("cited_sources: [3, 1] und [2;4] -> 1,2,3,4 (Reihenfolge wie gelesen)", f("A [3, 1]. B [2;4].") == [1, 2, 3, 4])
    ok("cited_sources: [2][9] -> nur 2 (9 nicht gelesen)", f("A [2][9].") == [2])
    ok("cited_sources: ohne Marken (nichts gefunden) -> leer", f("Dazu habe ich nichts gefunden.") == [] and f("") == [] and f(None) == [])
    ok("cited_sources: keine Verwechslung mit Zahlen/Links ([123], [x])", f("Seite [123] und [x] und 2026") == [])


def ask(model, q):
    K.setai(model=model)                                  # wartet den Zwischenspeicher der Einstellungen ab
    st, ev = K.sse("/api/ai/thread", U, {"q": q})
    src = evs(ev, "sources")
    done = K.get(ev, "done") or {}
    turn = [x for x in (K.req("GET", "/api/ai/thread", U)[1].get("turns") or []) if x.get("role") == "ai"][-1:]
    stored = ((turn[0].get("fields") or {}).get("sources") if turn else None)
    order = [t_ for t_, _d in ev] if isinstance(ev, list) else []
    return st, src, done, stored, order, K.text_of(ev)


def server():
    from saga import ai_saga
    auth.create_user(U, K.PW, U)
    auth.set_user_groups(auth.get_user(U)["id"], ["kw-quellen"])
    K.setai(wait=False, enabled=True, provider="openai", url=K.FAKE + "/v1", model="fake", pinned=["127.0.0.1"], groups=["kw-quellen"],
            caps={"ai_ideas": [], "ai_act": []}, quota=50, history=True, timeout=10, endpoint_note="")
    time.sleep(K.WAIT)
    st, d = K.req("POST", "/api/ai/consent", U, {"answer": "no_ideas", "version": ai_saga.CONSENT_V, "host": K.HN()})
    ok("Einwilligung erteilt", st == 200 and (d.get("consent") or {}).get("state") == "granted", (st, d))
    for i, t in enumerate(["Zitronenfalter Frühling", "Zitronenfalter Garten", "Zitronenfalter Raupe", "Zitronenfalter Futter",
                           "Zitronenfalter Winter"]):
        nid = K.req("POST", "/api/notes", U, {"title": t})[1]["id"]
        K.req("PATCH", f"/api/notes/{nid}", U, {"body": json.dumps({"blocks": [{"id": "b0", "type": "paragraph",
              "data": {"text": f"Der Zitronenfalter Nummer {i + 1} fliegt im März."}}]}), "format": "blocks", "rev": 0})
    time.sleep(1.2)                                       # Suchspeicher der Seiten

    st, src, done, stored, order, txt = ask("fake", "Wann fliegt der Zitronenfalter?")
    first, last = (src[0].get("sources") if src else None), (src[-1].get("sources") if src else None)
    ok("fake: 5 Seiten gelesen (erstes 'sources' fuer die [n]-Links), Antwort belegt nur [1]",
       st == 200 and first and len(first) == 5 and "[1]" in txt, (st, first and len(first), txt))
    ok("fake: zweites 'sources' direkt vor 'done' = nur Quelle 1", len(src) == 2 and [s["n"] for s in last or []] == [1]
       and order[-2:] == ["sources", "done"], (order, last))
    ok("fake: gespeicherter Zug (Verlauf/Apps) nur mit Quelle 1, Kontext-Ausweis read weiter alle 5, done ohne 'sources'",
       [s["n"] for s in stored or []] == [1] and len([r for r in done.get("read") or [] if str(r).startswith("note:")]) == 5
       and "sources" not in done, (stored, done))

    st, src, done, stored, order, txt = ask("fake-nichts", "What's coming up this week?")
    ok("fake-nichts: Negativ-Antwort -> letztes 'sources' leer, gespeicherter Zug ohne Quellen",
       st == 200 and "nichts gefunden" in txt and src and src[-1].get("sources") == [] and stored == [], (st, txt, src, stored))

    st, src, done, stored, order, txt = ask("fake-demo", "Wann fliegt der Zitronenfalter?")
    ok("fake-demo: alle 5 belegt -> 5 Quellen", st == 200 and len((src[-1].get("sources") if src else []) or []) == 5
       and len(stored or []) == 5, (txt, stored))


def main():
    K.cleanup()
    prev = dict(auth.settings(fresh=True).get("ai") or {})
    proc = subprocess.Popen([sys.executable, os.path.join(K.HERE, "fakeai.py"), str(K.PORT)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(80):
            try:
                socket.create_connection(("127.0.0.1", K.PORT), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        unit()
        server()
    finally:
        proc.terminate()
        auth.save_setting("ai", prev)
        auth.settings(fresh=True)
        K.cleanup()
    print(f"\nSaga Quellen (2542) {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
