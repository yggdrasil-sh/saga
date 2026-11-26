"""Knoepfe in Mitteilungen der Kollegin (2.6, Bugs-DB 2270 K-C): POST /api/ai/jobs/{id}/act, Einmal-Token ai_act_token
(saga/ai_act.py). Vertrag docs/APP-API-KI.md Abschnitt 12.3; die Faelle kommen aus tests/vectors/push_kollegin.json
("tokens.cases": Name, Zugang, Aktion -> Status, code, verbraucht) - Vektoren und Server bleiben so deckungsgleich.

Token stellt dieser Prozess wie die Zustellung aus (ai_act.issue mit dem echten Planstand); Auftraege: Rezept "aufgabe"
(ai_plan.plan ohne Modell) + Freigabe ueber HTTP (Worker des Servers legt die Aufgabe an) bzw. Zustaende direkt.
Prueft zusaetzlich: zwei gleichzeitige Freigaben = genau eine (FOR UPDATE), Ruecknahme loescht die Aufgabe wirklich,
"Spaeter" legt die Mitteilung in 3 h wieder an (again 1, hoechstens 2x), "Gerade nicht" bis zum naechsten 07:00 und
raeumt wartende job.* ab, act hinter dem Sammelpfad erreichbar (hkd_), Kontoloeschung raeumt Token, Klartext-Token nie in
der DB/im Log, Messung "Kollegin: act" (tools/perf/limits.json).
Aufruf (saga-test): PYTHONPATH=tests/pgshim:. python tests/kollegin_aktion.py   (Server mit SAGA_TEST_HOOKS/SAGA_TEST_TOKEN)."""
import base64
import http.client
import json
import os
import secrets
import statistics
import sys
import threading
import time
import urllib.parse

from itsdangerous import TimestampSigner

import auth
import db

db.wait_ready()
from db import connect  # noqa: E402
from saga import ai_act, ai_notify, ai_plan, ai_saga, jobs  # noqa: E402
from saga.actor import acting  # noqa: E402
from saga.core import _tok_hash, set_user_lang  # noqa: E402

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8900"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
A, B = "ka-anna", "ka-bert"
PW = "Ka-Pass-12345"
HERE = os.path.dirname(os.path.abspath(__file__))
VEC = json.load(open(os.path.join(HERE, "vectors", "push_kollegin.json"), encoding="utf-8"))["tokens"]
res = []
n5xx = [0]
K = {}                                    # Schluessel: hkd_A, hkd_B, hk_, Geraete-ids
ISSUED = []                               # alle ausgegebenen Klartext-Token (duerfen nie in DB/Log stehen)


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, "" if c else str(info)[:600], flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, cookie=None, body=None, hdr=None):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=30)
    h = {"Host": HOST, "Accept-Language": "de"}
    raw = None
    if cookie:
        h["Cookie"] = "saga=" + cookie
        h["X-Saga"] = "1"
    if body is not None:
        h["Content-Type"] = "application/json"
        raw = json.dumps(body)
    h.update(hdr or {})
    c.request(m, p, body=raw, headers=h)
    r = c.getresponse()
    data = r.read()
    if r.status >= 500:
        n5xx[0] += 1
    try:
        data = json.loads(data)
    except ValueError:
        data = data.decode("utf-8", "replace")
    c.close()
    return r.status, data


def act(jid, action, atok=None, pv=None, auth_="hkd_A", cookie=None):
    hdr = {}
    if auth_ in ("hkd_A", "hkd_B", "hk_"):
        hdr["Authorization"] = "Bearer " + K[auth_]
    elif auth_ == "sga_":
        hdr["Authorization"] = "Bearer sga_" + "x" * 40
    elif auth_ == "session":
        cookie = cookie or ck(A)
    body = {"action": action, "pv": pv}
    if atok is not None:
        body["atok"] = atok
    return req("POST", f"/api/ai/jobs/{jid}/act", cookie, body, hdr)


def keys():
    """Geraete-Anmeldungen A und B (hkd_), Widget-Schluessel (hk_), ein stream-Abo fuer Geraet A."""
    now = time.time()
    with connect() as c, c:
        for name in ("hkd_A", "hkd_B"):
            tok = "hkd_" + secrets.token_urlsafe(24)
            K[name] = tok
            K[name + "_id"] = c.execute("INSERT INTO device_login (uid, uname, name, hash, prefix, created) VALUES (?, ?, ?, ?, ?, ?)",
                                        (A, A, name, _tok_hash(tok), tok[:8], now)).lastrowid
        tok = "hk_" + secrets.token_urlsafe(24)
        K["hk_"] = tok
        c.execute("INSERT INTO app_token (uid, name, hash, prefix, created) VALUES (?, ?, ?, ?, ?)", (A, "widget", _tok_hash(tok), tok[:8], now))
        K["sub"] = c.execute("INSERT INTO push_sub (uid, kind, endpoint, p256dh, auth, name, device_id, created, caps) "
                             "VALUES (?, 'stream', ?, ?, ?, 'ka', ?, ?, 'update,detail,actions')",
                             (A, "stream:ka:" + secrets.token_hex(8), "BLHYxOr728enKRt5TD1CYvVNF0BuwiXR47U-gZNaQx5JZQmFYte8eAOzMYpk_-MURJmJiNUcbgtqdWav6xdJWJs",
                              "HEpVhwSc41SZINSabJrLHg", K["hkd_A_id"], now)).lastrowid


def st(jid):
    return jobs.state_of(jid) or {}


def token(jid, action, device="A", expires=None, again=0, plan_hash=None, plan_v=None, sub=True):
    s = st(jid)
    dev = K["hkd_A_id"] if device == "A" else (K["hkd_B_id"] if device == "B" else None)
    tok = ai_act.issue(A if s.get("uid", A) == A else s["uid"], jid, K["sub"] if sub else None, dev,
                       plan_v if plan_v is not None else s["plan_v"], plan_hash if plan_hash is not None else s["plan_hash"],
                       [action], expires if expires is not None else ai_act.now() + 3600, again)[action]
    ISSUED.append(tok)
    return tok


def page(cookie, title):
    nid = req("POST", "/api/notes", cookie, {"title": title})[1]["id"]
    body = json.dumps({"blocks": [{"id": "b0", "type": "paragraph", "data": {"text": "Start"}}]})
    req("PATCH", f"/api/notes/{nid}", cookie, {"body": body, "format": "blocks", "rev": 0})
    return nid


def recipe_job(text, uid=A):
    """Rezept "eintragen" (append_to_page, Klasse own/undo - aus der Mitteilung freigeb- und ruecknehmbar; "aufgabe" ist
    nur loeschbar und bekommt deshalb nie approve/undo)."""
    r = ai_plan.plan(uid, "", recipe="eintragen", fields={"page": page(ck(uid), "ka-Seite " + text), "text": text})
    return r["job"]


def page_text(jid):
    with connect() as c:
        r = c.execute("SELECT j.plan FROM job j WHERE j.id=?", (jid,)).fetchone()
        pid = c.execute("SELECT args->>'id' FROM job_step WHERE job_id=? ORDER BY n LIMIT 1", (jid,)).fetchone()[0]
        return (c.execute("SELECT body FROM note WHERE id=?", (int(pid),)).fetchone() or [""])[0] or "", int(pid), r


def approve_http(jid, cookie):
    s = st(jid)
    return req("POST", f"/api/ai/jobs/{jid}/approve", cookie, {"plan_v": s["plan_v"], "plan_hash": s["plan_hash"]})


def wait_state(jid, want, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if st(jid).get("state") == want:
            return True
        time.sleep(0.2)
    return False


def done_job(text, cookie):
    jid = recipe_job(text)
    approve_http(jid, cookie)
    return jid if wait_state(jid, "erledigt") else None


def plain_job(state="wartet_dich", steps=None, uid=A):
    jid = jobs.create(uid, ai_plan.KIND, title="ka-Auftrag", steps=steps or [{"tool": "create_task", "args": {"text": "x"}}])
    path = {"wartet_ok": (), "wartet_dich": ("laeuft", "wartet_dich"), "erledigt": ("laeuft", "erledigt")}[state]
    with acting(None), connect() as c, c:
        cur = "wartet_ok"
        for new in path:
            assert jobs._to(c, jid, cur, new, jobs.now() - 300, reason="ai" if new == "wartet_dich" else None)
            cur = new
    return jid


def used(tok):
    with connect() as c:
        r = c.execute("SELECT used_at FROM ai_act_token WHERE hash=?", (ai_act.tok_hash(tok),)).fetchone()
    return r is not None and r[0] is not None


def run_case(cs, cA, cB):
    """Ein Fall aus den Vektoren -> (status, body, verbraucht)."""
    n = cs["name"]
    if n in ("undo_ok", "undo_again"):
        if "undo" not in K:
            jid = done_job("ka-Rueckgaengig", cA)
            K["undo"] = (jid, token(jid, "undo"))
        jid, tok = K["undo"]
        before = used(tok)
        if n == "undo_ok":
            ok("undo_ok: Eintrag steht vorher auf der Seite", "ka-Rueckgaengig" in page_text(jid)[0])
        s, d = act(jid, "undo", tok, st(jid)["plan_v"])
        if n == "undo_ok":
            ok("undo_ok: Eintrag wirklich weg, Zustand zurueckgenommen", s == 200 and d.get("state") == "zurueckgenommen" and
               "ka-Rueckgaengig" not in page_text(jid)[0], (d, page_text(jid)[0][:200]))
        return s, d, used(tok) and not before
    if n == "undo_conflict":
        jid = done_job("ka-Konflikt", cA)
        _b, pid, _p = page_text(jid)
        with connect() as c:
            rev = c.execute("SELECT rev FROM note WHERE id=?", (pid,)).fetchone()[0]
        body = json.dumps({"blocks": [{"id": "b0", "type": "paragraph", "data": {"text": "von Hand geaendert"}}]})
        sp, _dp = req("PATCH", f"/api/notes/{pid}", cA, {"body": body, "format": "blocks", "rev": rev})
        tok = token(jid, "undo")
        s, d = act(jid, "undo", tok, st(jid)["plan_v"])
        return s, d, used(tok)
    if n in ("approve_ok", "approve_stale_pv", "approve_replanned", "approve_state"):
        jid = recipe_job(f"ka-{n}")
        if n == "approve_state":
            approve_http(jid, cA)
            wait_state(jid, "erledigt")
            tok = token(jid, "approve", plan_v=1, plan_hash=st(jid)["plan_hash"])
        elif n == "approve_replanned":
            tok = token(jid, "approve", plan_hash="0" * 64)
        else:
            tok = token(jid, "approve")
        pv = st(jid)["plan_v"] + (1 if n == "approve_stale_pv" else 0)
        s, d = act(jid, "approve", tok, pv)
        if n == "approve_ok":
            ok("approve_ok: Auftrag laeuft/erledigt", wait_state(jid, "erledigt"), st(jid))
        elif st(jid).get("state") == "wartet_ok":
            jobs.cancel(jid, A, "person")
        return s, d, used(tok)
    if n == "approve_out":
        jid = plain_job("wartet_ok", steps=[{"tool": "create_task", "args": {}}, {"tool": "send_message", "args": {}, "klass": "out",
                                                                                  "undo_class": "final"}])
        tok = token(jid, "approve")
        s, d = act(jid, "approve", tok, st(jid)["plan_v"])
        jobs.cancel(jid, A, "person")
        return s, d, used(tok)
    if n == "later_ok":
        jid = plain_job("wartet_dich")
        tok = token(jid, "later")
        t0 = time.time()
        s, d = act(jid, "later", tok, st(jid)["plan_v"])
        with connect() as c:
            q = c.execute("SELECT next_at, data FROM push_queue WHERE uid=? AND kind='job.needs_you'", (A,)).fetchall()
        later = [json.loads(x[1]) for x in q if abs(x[0] - (t0 + 3 * 3600)) < 60]
        ok("later: Mitteilung in 3 h erneut vorgemerkt (again 1), Antwort at", later and later[0] == {"job": jid, "again": 1} and
           isinstance(d.get("at"), int), (q, d))
        tok2 = token(jid, "later", again=2)
        s2, d2 = act(jid, "later", tok2, st(jid)["plan_v"])
        ok("later hoechstens 2x: dritter Druck -> 409 state", s2 == 409 and d2.get("code") == "state", (s2, d2))
        jobs.cancel(jid, A, "person")
        return s, d, used(tok)
    if n == "quiet_ok":
        jid = plain_job("erledigt")
        with connect() as c, c:
            c.execute("INSERT INTO push_queue (uid, sub_id, kind, data, created, next_at) VALUES (?, ?, 'job.done', ?, ?, ?)",
                      (A, K["sub"], json.dumps({"job": jid}), time.time(), time.time() + 3600))
        tok = token(jid, "quiet")
        t0 = time.time()
        s, d = act(jid, "quiet", tok, st(jid)["plan_v"])
        want = ai_saga.next_local(7, None, t0)
        with connect() as c:
            left = c.execute("SELECT COUNT(*) FROM push_queue WHERE uid=? AND kind IN ('job.needs_you', 'job.done', 'job.failed')",
                             (A,)).fetchone()[0]
        ok(f"quiet: bis naechstes 07:00 ({d.get('until')} = {want}), quiet_until gesetzt, wartende job.* weg",
           d.get("until") == want and ai_saga.quiet_until(A, "push") == want and left == 0, (d, left))
        ai_saga.set_quiet(A, "push", None)
        return s, d, used(tok)
    if n == "expired":
        jid = plain_job("wartet_dich")
        tok = token(jid, "later", expires=ai_act.now() - 5)
        s, d = act(jid, "later", tok, st(jid)["plan_v"])
        jobs.cancel(jid, A, "person")
        return s, d, used(tok)
    if n == "sub_gone":
        jid = plain_job("wartet_dich")
        with connect() as c, c:
            sid = c.execute("INSERT INTO push_sub (uid, kind, endpoint, p256dh, auth, name, device_id, created) VALUES "
                            "(?, 'stream', ?, 'x', 'y', 'weg', ?, ?)", (A, "stream:weg:" + secrets.token_hex(6), K["hkd_A_id"], time.time())).lastrowid
        tok = ai_act.issue(A, jid, sid, K["hkd_A_id"], st(jid)["plan_v"], st(jid)["plan_hash"], ["later"], ai_act.now() + 3600)["later"]
        ISSUED.append(tok)
        with connect() as c, c:
            c.execute("DELETE FROM push_sub WHERE id=?", (sid,))
        s, d = act(jid, "later", tok, st(jid)["plan_v"])
        jobs.cancel(jid, A, "person")
        return s, d, False
    if n == "wrong_action":
        jid = plain_job("erledigt")
        tok = token(jid, "later")
        s, d = act(jid, "quiet", tok, st(jid)["plan_v"])
        return s, d, used(tok)
    if n == "device_other":
        jid = plain_job("wartet_dich")
        tok = token(jid, "later", device="A")
        s, d = act(jid, "later", tok, st(jid)["plan_v"], auth_="hkd_B")
        jobs.cancel(jid, A, "person")
        return s, d, used(tok)
    if n in ("device_less_hkd", "session_web"):
        jid = plain_job("wartet_dich")
        tok = token(jid, "later", device=None, sub=False)
        s, d = act(jid, "later", tok, st(jid)["plan_v"], auth_="hkd_A" if n == "device_less_hkd" else "session", cookie=cA)
        jobs.cancel(jid, A, "person")
        return s, d, used(tok)
    if n == "no_ai":
        jid = plain_job("erledigt")
        tok = token(jid, "quiet")
        cur = dict(auth.settings(fresh=True)["ai"])
        auth.save_setting("ai", dict(cur, groups=["ka-niemand"]))
        time.sleep(5.6)
        s, d = act(jid, "quiet", tok, st(jid)["plan_v"])
        auth.save_setting("ai", cur)
        time.sleep(5.6)
        return s, d, used(tok)
    if n in ("widget_later", "widget_undo"):
        jid = plain_job("wartet_dich" if n == "widget_later" else "erledigt")
        a = cs["action"]
        tok = token(jid, a)
        s, d = act(jid, a, tok, st(jid)["plan_v"], auth_="hk_")
        if st(jid).get("state") == "wartet_dich":
            jobs.cancel(jid, A, "person")
        return s, d, used(tok)
    if n == "api_token":
        jid = plain_job("wartet_dich")
        tok = token(jid, "later")
        s, d = act(jid, "later", tok, st(jid)["plan_v"], auth_="sga_")
        jobs.cancel(jid, A, "person")
        return s, d, used(tok)
    if n == "foreign":
        jid = plain_job("erledigt", uid=B)
        tok = ai_act.issue(B, jid, None, None, 1, st(jid)["plan_hash"], ["undo"], ai_act.now() + 3600)["undo"]
        ISSUED.append(tok)
        s, d = act(jid, "undo", tok, 1)
        return s, d, used(tok)
    if n == "reserved":
        jid = plain_job("erledigt")
        s, d = act(jid, "retry", None, st(jid)["plan_v"])
        return s, d, False
    if n == "open":
        jid = plain_job("erledigt")
        s, d = act(jid, "open", None, None)
        ok("open: Adresse des Auftrags", d.get("url") == f"/saga?tab=jobs&job={jid}", d)
        return s, d, False
    raise AssertionError("unbekannter Fall " + n)


def part_cases(cA, cB):
    for cs in VEC["cases"]:
        try:
            s, d, u = run_case(cs, cA, cB)
        except Exception as ex:  # noqa: BLE001
            ok(f"{cs['name']}: Ausnahme", False, f"{type(ex).__name__}: {ex}")
            continue
        code = d.get("code") if isinstance(d, dict) else None
        ok(f"{cs['name']} ({cs['auth']}, {cs['action']}): {s}/{code} verbraucht={u} - erwartet {cs['status']}/{cs['code']}/{cs['consumed']}",
           s == cs["status"] and (cs["code"] is None or code == cs["code"]) and u == cs["consumed"], d)
        if s == 409:
            ok(f"{cs['name']}: 409 mit url", isinstance(d, dict) and str(d.get("url", "")).startswith("/saga?tab=jobs&job="), d)


def part_race(cA):
    jid = recipe_job("ka-Gleichzeitig")
    tok = token(jid, "approve")
    out = []

    def go():
        out.append(act(jid, "approve", tok, st(jid)["plan_v"]))
    th = [threading.Thread(target=go) for _ in range(2)]
    for t_ in th:
        t_.start()
    for t_ in th:
        t_.join()
    codes = sorted(s for s, _ in out)
    ok(f"zwei gleichzeitige Freigaben: genau eine 200, die andere 410 ({codes})", codes == [200, 410], out)
    wait_state(jid, "erledigt")
    ok("Weg act liegt vor dem Sammelpfad (/api/ai/jobs/{rest}) - mit hkd_ erreichbar", codes[0] == 200)


def part_perf():
    jid = plain_job("wartet_dich")
    toks = [token(jid, "later") for _ in range(40)]
    times = []
    for tk in toks:
        t0 = time.perf_counter()
        s, _ = act(jid, "later", tk, st(jid)["plan_v"])
        times.append(time.perf_counter() - t0)
        if s != 200:
            ok("perf: act 200", False, s)
            break
    jobs.cancel(jid, A, "person")
    times.sort()
    p95 = times[min(len(times) - 1, int(round(0.95 * (len(times) - 1))))] * 1000
    lim = json.load(open(os.path.join(HERE, "..", "tools", "perf", "limits.json")))["S"].get("Kollegin: act")
    print("PERF " + json.dumps({"name": "Kollegin: act (later, hkd_)", "group": "kollegin", "n": len(times),
                                "p50_ms": round(statistics.median(times) * 1000, 2), "p95_ms": round(p95, 2), "stufe": "S", "limit": lim}),
          flush=True)
    ok(f"Kollegin: act p95 {p95:.1f} ms <= {lim} ms (Stufe S)", lim is not None and p95 <= lim)


def part_cleanup_and_secrets():
    with connect() as c:
        rows = json.dumps(c.execute("SELECT * FROM ai_act_token").fetchall(), default=str)
    ok(f"Klartext-Token nie in der DB ({len(ISSUED)} geprueft)", ISSUED and not any(tk in rows for tk in ISSUED))
    lp = os.environ.get("HK_TEST_LOG")
    if lp and os.path.exists(lp):
        log = open(lp, encoding="utf-8", errors="replace").read()
        ok("Klartext-Token nie im Server-Log", not any(tk in log for tk in ISSUED))
    with connect() as c, c:
        c.execute("INSERT INTO ai_act_token (hash, uid, job_id, plan_v, action, expires, created) VALUES (?, ?, ?, 1, 'quiet', ?, ?)",
                  ("alt" + secrets.token_hex(8), A, plain_job("erledigt"), ai_act.now() - 3 * 86400, ai_act.now() - 3 * 86400))
    n = ai_act.cleanup()
    ok(f"cleanup raeumt abgelaufene/benutzte Token ({n})", n >= 1)
    from saga.userdata import delete_user_data
    delete_user_data(B)
    with connect() as c:
        nb = c.execute("SELECT COUNT(*) FROM ai_act_token WHERE uid=?", (B,)).fetchone()[0]
    ok("Kontoloeschung raeumt die Token", nb == 0, nb)


def cleanup():
    with connect() as c, c:
        for u in (A, B):
            for tbl in ("ai_act_token", "push_queue", "push_sub", "job", "task", "notification", "device_login", "app_token"):
                c.execute(f"DELETE FROM {tbl} WHERE uid=?", (u,))
            c.execute("DELETE FROM user_kv WHERE uid=? AND k IN ('ai_quiet', 'status_held')", (u,))


def main():
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("push", "ai", "ai_persona", ai_plan.SETTING)}
    try:
        auth.save_setting("push", {"enabled": True, "webpush": True, "ntfy": True, "unifiedpush": True, "stream": True})
        auth.save_setting("ai", dict(prev.get("ai") or {}, enabled=True, provider="openai", url="http://127.0.0.1:9/v1", model="x",
                                     pinned=["127.0.0.1"], groups=[], caps={}))
        auth.save_setting(ai_plan.SETTING, {"level": "plans"})
        for u in (A, B):
            if not auth.get_user(u):
                auth.create_user(u, PW, u)
            set_user_lang(u, "de")
        cleanup()
        auth.settings(fresh=True)
        for u in (A, B):
            ai_saga.grant(u)
        keys()
        time.sleep(5.6)
        cA, cB = ck(A), ck(B)
        part_cases(cA, cB)
        part_race(cA)
        part_perf()
        part_cleanup_and_secrets()
    finally:
        cleanup()
        for k, v in keep.items():
            if v is None:
                with connect() as c, c:
                    c.execute("DELETE FROM setting WHERE k=?", (k,))
            else:
                auth.save_setting(k, v)
    ok("Keine Serverfehler (HTTP 5xx)", n5xx[0] == 0, n5xx[0])
    good = sum(res)
    print(f"KOLLEGIN-AKTION {good}/{len(res)} - " + ("ALLES GRUEN" if good == len(res) else "FEHLER"), flush=True)
    sys.exit(0 if good == len(res) else 1)


if __name__ == "__main__":
    _ = ai_notify
    main()
