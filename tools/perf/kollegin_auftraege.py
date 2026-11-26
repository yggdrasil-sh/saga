"""Lastmessungen Auftraege der Kollegin (Saga 2.6, Bugs-DB 2267 K-B, Architektur §5 Grenzwerte) - Server-Anteil ohne
Modellzeit, gegen tools/perf/limits.json (Praefix des Namens):

  Kollegin: Plan pruefen (Planer)          ai_plan.check + rights (Schema/Registry/Rechte, 1 Schritt) im Prozess
  Kollegin: POST /api/ai/jobs bis Plan     ai_plan.plan im Prozess, Modellaufruf durch eine feste Antwort ersetzt
                                           (Kontext-Auswahl, Pruefung, Auftrag anlegen, Plan-Ansicht)
  Kollegin: approve                        POST /api/ai/jobs/{id}/approve ueber HTTP (HK_TEST_BASE)
  Kollegin: GET /api/ai/jobs               Liste ueber HTTP mit 150 beendeten + 5 offenen Auftraegen

    PYTHONPATH=tests/pgshim:. python tools/perf/kollegin_auftraege.py [--n 40] [--stage S]
Wegwerf-Konto ka-perf (200 Seiten), KI-Einstellung kurz an, danach wie vorher. Ausgabe "PERF {...}" je Messung,
Ende-Code 1 bei Ueberschreitung."""
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "tests"))


def q(times, p):
    return times[min(len(times) - 1, int(round(p * (len(times) - 1))))]


def res(name, times, stage):
    times = sorted(times)
    lim = json.load(open(os.path.join(HERE, "limits.json"))).get(stage, {})
    pre = max((k for k in lim if name.startswith(k)), key=len, default=None)
    return {"name": name, "group": "kollegin", "n": len(times), "p50_ms": round(statistics.median(times) * 1000, 2),
            "p95_ms": round(q(times, 0.95) * 1000, 2), "max_ms": round(times[-1] * 1000, 2), "stufe": stage,
            "limit": lim.get(pre) if pre else None}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--stage", default="S")
    a = ap.parse_args()
    import db
    db.wait_ready()
    import auth
    import ki as K
    from saga import ai, ai_plan, api_v1, apitok, i18n, jobs
    from saga.userdata import delete_user_data
    uid = "ka-perf"
    prev = dict(auth.settings(fresh=True).get("ai") or {})
    prev_plan = dict(auth.settings(fresh=True).get(ai_plan.SETTING) or {})
    out, bad = [], False
    try:
        if auth.get_user(uid):
            delete_user_data(uid, drop_user=True)
        auth.create_user(uid, "Ka-Perf-Pass-12345", uid)
        auth.save_setting("ai", dict(prev, enabled=True, provider="openai", url="http://127.0.0.1:9/v1", model="fake-plan", quota=0, caps={},
                                     groups=[], pinned=["127.0.0.1"]))
        auth.save_setting(ai_plan.SETTING, {"level": "plans"})
        auth.settings(fresh=True)
        i18n.set_lang("de")
        full = apitok.Tok(10 ** 9 + 11, uid, "perf", "sga_perf", apitok.SCOPES, None, None)
        fest = api_v1.page_create(full, {"title": "Sommerfest", "markdown": "Pavillon und Grillkohle sind noch offen."})[1]["id"]
        for i in range(200):
            api_v1.page_create(full, {"title": f"Notiz {i}", "markdown": f"Inhalt {i} Grill Kohle Garten Fest {i % 7}"})
        # 1) Plan pruefen
        cand = {"kind": "plan", "steps": [{"tool": "append_to_page", "args": {"id": fest, "markdown": "- Ben bringt die Grillkohle"}}]}
        rs = ai_plan.recipes()
        times = []
        for k in range(a.n + 3):
            t0 = time.perf_counter()
            kind, r, probs = ai_plan.check(uid, cand, "plans", [(fest, "Sommerfest")], rs)
            probs = probs or ai_plan.rights(uid, r["steps"], [f"note:{fest}"])
            if k >= 3:
                times.append(time.perf_counter() - t0)
        out.append(res("Kollegin: Plan pruefen (Planer, 1 Schritt)", times, a.stage))
        # 2) POST /api/ai/jobs bis Plan (ohne Modell)
        answer = json.dumps({"kind": "plan", "title": "Grillkohle", "steps": [{"tool": "create_task", "args": {"text": "Grillkohle kaufen"}}]})
        real = ai.stream_metered

        def fake_stream(uid_, pool, c, msgs, emit, stop=None, **kw):
            emit(answer)
            return {"cut": False}
        ai.stream_metered = fake_stream
        times = []
        try:
            for k in range(a.n + 3):
                t0 = time.perf_counter()
                r = ai_plan.plan(uid, "Kümmer dich um die Grillkohle für das Sommerfest")
                dt = time.perf_counter() - t0
                if r.get("type") != "plan":
                    bad = True
                    print("FAIL Plan:", r, flush=True)
                    break
                jobs.cancel(r["job"], uid)
                if k >= 3:
                    times.append(dt)
        finally:
            ai.stream_metered = real
        out.append(res("Kollegin: POST /api/ai/jobs bis Plan (ohne Modell)", times, a.stage))
        # 3) approve ueber HTTP; 4) GET /api/ai/jobs
        cookie = K.ck(uid)
        for i in range(150):
            j = jobs.create(uid, ai_plan.KIND, title=f"Alt {i}", steps=[{"tool": "create_task", "args": {"text": f"Alt {i}"}}])
            jobs.cancel(j, uid)
        time.sleep(K.WAIT)
        times = []
        for k in range(a.n + 3):
            r = ai_plan._store(uid, {"title": "Freigabe", "steps": [{"tool": "create_task", "args": {"text": f"F {k}"}, "klass": "own",
                                                                         "undo_class": "delete_only"}], "recipe": None}, [], "plans", "x", "saga")
            t0 = time.perf_counter()
            s, d = K.req("POST", f"/api/ai/jobs/{r['job']}/approve", cookie, {"plan_v": r["plan_v"], "plan_hash": r["plan_hash"]})
            dt = time.perf_counter() - t0
            jobs.cancel(r["job"], uid)
            if s != 200:
                bad = True
                print("FAIL approve:", s, d, flush=True)
                break
            if k >= 3:
                times.append(dt)
        out.append(res("Kollegin: approve", times, a.stage))
        for i in range(5):
            ai_plan._store(uid, {"title": f"Offen {i}", "steps": [{"tool": "create_task", "args": {"text": f"O {i}"}, "klass": "own",
                                                                   "undo_class": "delete_only"}], "recipe": None}, [], "plans", "x", "saga")
        times = []
        for k in range(a.n + 3):
            t0 = time.perf_counter()
            s, d = K.req("GET", "/api/ai/jobs", cookie)
            dt = time.perf_counter() - t0
            if s != 200 or not d["counts"]["fertig"]:
                bad = True
                print("FAIL Liste:", s, str(d)[:200], flush=True)
                break
            if k >= 3:
                times.append(dt)
        out.append(res("Kollegin: GET /api/ai/jobs (155 Auftraege)", times, a.stage))
    finally:
        auth.save_setting("ai", prev)
        auth.save_setting(ai_plan.SETTING, prev_plan)
        if auth.get_user(uid):
            delete_user_data(uid, drop_user=True)
    for r in out:
        print("PERF " + json.dumps(r, ensure_ascii=False), flush=True)
        over = r["limit"] is not None and r["p95_ms"] > r["limit"]
        bad |= over
        print(("FAIL" if over else "OK  "), f"{r['name']}: p50 {r['p50_ms']} ms, p95 {r['p95_ms']} ms (Grenze {a.stage}: {r['limit']} ms)", flush=True)
    return 1 if bad or len(out) < 4 else 0


if __name__ == "__main__":
    sys.exit(main())
