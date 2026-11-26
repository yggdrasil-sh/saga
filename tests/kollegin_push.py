"""Mitteilungen der Kollegin zu Auftraegen (2.6, Bugs-DB 2270 K-C): saga/ai_notify.py, saga/push.py (job.*), Nutzlast 3.
Vertrag docs/APP-API.md "Push-Nutzlast 3 und Auftraege", Vektoren tests/vectors/push_kollegin.json.

Wechsel der Auftraege macht dieser Prozess selbst (saga.jobs._to, mit saga.ai_notify geladen: Glocke + Vormerken hier),
zugestellt wird vom Server (HK_TEST_BASE) an Fake-Empfaenger in diesem Prozess (127.0.0.1:KC_FAKE_PORT, Standard 8909):
Web Push und UnifiedPush mit Schluesseln (entschluesselt RFC 8291), UnifiedPush ohne Schluessel und ntfy (Klartext).

Prueft: /api/push payload 3 + sub_caps, features jobpush1 · je Uebergang genau eine Art (needs_you, done, failed), nichts
bei abgebrochen (aus wartet_ok, Herkunft saga) · Wortlaut exakt (title/body/t1/t2) · Kennungen, detail, actions nur auf
E2E-Wegen mit caps; Klartext-Wege (ntfy, UnifiedPush ohne Schluessel, APNs direkt ohne E2E) ohne Kennung/Inhalt/Knoepfe ·
Topic undurchsichtig, Urgency, Auffuellen · notify_content never · Bereich jobs aus · "Gerade nicht" (quiet) · Nicht
stoeren + Sammelmeldung digest.jobs · Beschaeftigt ohne Ton -> silent · nie VoIP · 60-s-Fenster nach der Freigabe ·
update removed bei Ruecknahme · Glocke Text/Ziel/Ersetzen/Entfernen · Auftrag von aussen (Herkunft share) -> needs.ok
mit approve · Groesse (Thai) · Kanarien (Titel, Seitentitel, Name): positiv im entschluesselten detail und in der Glocke,
nie auf Klartext-Wegen, in push_queue.data, ai_act_token, im Server-Log (HK_TEST_LOG, falls gesetzt).
Aufruf (saga-test): PYTHONPATH=tests/pgshim:. python tests/kollegin_push.py   (Server mit SAGA_TEST_HOOKS/SAGA_TEST_TOKEN)."""
import base64
import http.client
import http.server
import json
import os
import sys
import threading
import time
import urllib.parse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from itsdangerous import TimestampSigner

import auth
import db

db.wait_ready()
from db import connect  # noqa: E402
from saga import ai_notify, ai_plan, ai_saga, ai_say, jobs, push, tasks, testclock  # noqa: E402
from saga.actor import acting  # noqa: E402
from saga.core import set_user_lang  # noqa: E402

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8900"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
BASE = os.environ["BASE_URL"].rstrip("/")
S = TimestampSigner(os.environ["SESSION_SECRET"])
FP = int(os.environ.get("KC_FAKE_PORT", "8909"))
FAKE = f"http://127.0.0.1:{FP}"
A = "kp-anna"
PW = "Kp-Pass-12345"
CAN_T = "Grillkohle-KANARIE-T7"          # Auftragstitel
CAN_P = "Sommerfest-KANARIE-P3"          # Titel einer Aufgabe/Seite im Ergebnis
CAN_N = "Mira-KANARIE-N5"                # Name einer Person im Plan
VEC = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "vectors", "push_kollegin.json"), encoding="utf-8"))
res = []
got = []                                 # (name, inhalt, koepfe, laenge)
n5xx = [0]


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, "" if c else str(info)[:600], flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, cookie=None, body=None, hdr=None, lang="de"):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=30)
    h = {"Host": HOST, "Accept-Language": lang}
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


def clock(**b):
    tok = os.environ.get("SAGA_TEST_TOKEN") or ""
    return req("POST", "/_test/clock", body=b, hdr={"X-Saga-Test": tok})


# --- Fake-Empfaenger ---------------------------------------------------------------------------------------------
KEYS = {}


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def new_keys(name):
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    sec = os.urandom(16)
    KEYS[name] = (priv, pub, sec)
    return {"p256dh": b64u(pub), "auth": b64u(sec)}


def decrypt(name, body):
    priv, ua_pub, sec = KEYS[name]
    salt, idlen = body[:16], body[20]
    as_pub = body[21:21 + idlen]
    ct = body[21 + idlen:]
    shared = priv.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
    ikm = HKDF(hashes.SHA256(), 32, sec, b"WebPush: info\x00" + ua_pub + as_pub).derive(shared)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    rec = AESGCM(cek).decrypt(nonce, ct, None)
    pt = rec.rstrip(b"\x00")
    assert pt.endswith(b"\x02")
    return json.loads(pt[:-1]), len(rec)


class Fake(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        name = self.path.rstrip("/").rsplit("/", 1)[-1]
        try:
            if name in KEYS:
                body, rec = decrypt(name, raw)
            else:
                body, rec = json.loads(raw), None
                if self.path.startswith("/ntfy"):
                    name = "ntfy:" + str(body.get("topic"))
            got.append((name, body, dict(self.headers), rec, raw))
        except Exception as ex:  # noqa: BLE001
            got.append((name, {"error": str(ex)}, {}, None, raw))
        self.send_response(201)
        self.end_headers()

    def log_message(self, *a):
        pass


def wait_for(name, pred, since, timeout=16.0):
    end = time.time() + timeout
    while time.time() < end:
        for g in got[since:]:
            if g[0] == name and pred(g[1]):
                return g
        time.sleep(0.2)
    return None


def of(name, since, kinds=None):
    return [g for g in got[since:] if g[0] == name and (kinds is None or g[1].get("kind") in kinds)]


# --- Auftraege ---------------------------------------------------------------------------------------------------
def mk(title=CAN_T, source="saga", steps=None, due_at=None):
    return jobs.create(A, ai_plan.KIND, source=source, title=title, plan={"say": {"person": CAN_N}},
                       steps=steps or [{"tool": "create_task", "args": {"text": CAN_P}}], due_at=due_at)


def move(jid, *path, at=None, reason=None):
    """Zustaende in EINER Transaktion durchlaufen (laeuft wird nie sichtbar - kein Worker greift zu). at: Zeit je Schritt."""
    with acting(None), connect() as c, c:
        cur = c.execute("SELECT state FROM job WHERE id=?", (jid,)).fetchone()[0]
        for i, new in enumerate(path):
            t = (at[i] if at else None) or jobs.now()
            assert jobs._to(c, jid, cur, new, t, reason=reason if new != "laeuft" else None), (cur, new)
            cur = new


def change(jid, text=CAN_P):
    """Aenderung wie ein Schritt: echte Aufgabe + job_change (task_delete -> zuruecknehmbar)."""
    from saga import ai_undo
    tid = json.loads(tasks.task_add(A, {"text": text}).body)["id"]
    with connect() as c, c:
        r = ai_undo._task(c, tid)
        fp = ai_undo._task_fp(r)
        c.execute("INSERT INTO job_change (job_id, step_n, idem_key, attempt, target, at, before, rev_after) "
                  "VALUES (?, 0, ?, 'a1', ?, ?, CAST(? AS JSONB), ?)",
                  (jid, f"kp:{jid}:{tid}", f"task:{tid}", jobs.now(), json.dumps({"op": "task_delete"}), fp))
    return tid, r


def done_job(title=CAN_T, steps=None, quick=False):
    jid = mk(title, steps=steps)
    change(jid)
    now = jobs.now()
    move(jid, "laeuft", "erledigt", at=[now - (10 if quick else 120), now])
    return jid


def bell(cookie, lang="de"):
    s, d = req("GET", "/api/notifications?limit=50", cookie, lang=lang)
    return [x for x in (d.get("items") or []) if str(x.get("kind", "")).startswith("job.")] if s == 200 else []


# --- Ablauf ------------------------------------------------------------------------------------------------------
SUBS = {}


def subscribe(cA):
    for name, typ, caps, keys, path in (("w1", "webpush", ["update", "detail", "actions"], True, "/wp/w1"),
                                        ("w0", "webpush", [], True, "/wp/w0"),
                                        ("u1", "unifiedpush", ["update", "detail", "actions"], True, "/upk/u1"),
                                        ("u0", "unifiedpush", ["update", "detail", "actions"], False, "/up/u0"),
                                        ("nt", "ntfy", [], False, "/ntfy/kc-topic")):
        b = {"type": typ, "name": name, "caps": caps}
        if typ == "ntfy":
            b["url"] = FAKE + path
        else:
            b["endpoint"] = FAKE + path
        if keys:
            b["keys"] = new_keys(name)
        s, d = req("POST", "/api/push/subscriptions", cA, b)
        SUBS[name] = d.get("id") if isinstance(d, dict) else None
        ok(f"Abo {name} ({typ}, caps {caps}) angelegt ({s})", s in (200, 201), d)


def part_api(cA):
    s, d = req("GET", "/api/push", cA)
    ok("GET /api/push: payload 3, sub_caps mit detail/actions, Bereich jobs", s == 200 and d["server"].get("payload") == 3 and
       d["server"].get("sub_caps") == ["update", "widgets", "detail", "actions"] and "jobs" in d.get("categories", []), d.get("server"))
    s, v = req("GET", "/api/version")
    ok("features enthaelt jobpush1", s == 200 and "jobpush1" in (v.get("features") or []), v)


def part_needs(cA):
    n0 = len(got)
    jid = mk()
    move(jid, "laeuft", "wartet_dich", reason="ai")
    g = wait_for("w1", lambda m: m.get("job") == jid, n0)
    ok("wartet_dich -> job.needs_you an w1", g is not None and g[1]["kind"] == "job.needs_you", g and g[1])
    m = g[1] if g else {}
    tv = VEC["texts"]["de"]
    ok("Wortlaut neutral (title/body wie Vektoren)", m.get("title") == tv["title"]["needs"] and m.get("body") == tv["body"], m)
    ok("Kennungen auf E2E: url/tag/group_key/thread/job/state/pv, v 3",
       m.get("url") == f"/saga?tab=jobs&job={jid}" and m.get("tag") == f"job-{jid}" and m.get("group_key") == f"job:{jid}" and
       m.get("thread") == f"job-{jid}" and m.get("state") == "needs" and m.get("pv") == 1 and m.get("v") == 3, m)
    d = m.get("detail") or {}
    ok("detail t1/t2 exakt (Wartet auf dich: Titel / Satz 'recht')", d.get("t1") == f"Wartet auf dich: {CAN_T}" and
       d.get("t2") == VEC["texts"]["de"]["t2"]["needs.recht"] and d.get("b") == "fail" and d.get("lang") == "de", d)
    acts = [a["a"] for a in m.get("actions") or []]
    ok("Knoepfe: nur later (Token 24 Zeichen)", acts == ["later"] and all(len(a["t"]) == 24 for a in m["actions"]), m.get("actions"))
    h = g[2] if g else {}
    ok("Topic undurchsichtig (= collapse-id), Urgency high", h.get("Topic") == push.e2e_collapse(b64u(KEYS["w1"][2]), f"job-{jid}")[:32]
       and "job" not in (h.get("Topic") or "job") and h.get("Urgency") == "high", h)
    ok(f"aufgefuellt (Datensatz {g and g[3]} in 512/1024/2048)", g is not None and g[3] in (512, 1024, 2048))
    g0 = wait_for("w0", lambda m: m.get("job") == jid, n0)
    ok("w0 (ohne caps): Kennungen ja, detail/actions nein", g0 is not None and "detail" not in g0[1] and "actions" not in g0[1] and
       g0[1].get("tag") == f"job-{jid}", g0 and g0[1])
    gu = wait_for("u1", lambda m: m.get("job") == jid, n0)
    ok("UnifiedPush mit Schluesseln wie Web Push (detail, actions)", gu is not None and gu[1].get("detail") and gu[1].get("actions"),
       gu and gu[1])
    gp = wait_for("u0", lambda m: m.get("kind") == "job.needs_you", n0)
    ok("UnifiedPush ohne Schluessel: Klartext ohne Kennung/detail/actions/pv, tag saga-job",
       gp is not None and not any(k in gp[1] for k in ("job", "detail", "actions", "pv", "group_key", "thread")) and
       gp[1].get("tag") == "saga-job" and gp[1].get("url") == "/saga?tab=jobs" and gp[1].get("state") == "needs", gp and gp[1])
    gn = wait_for("ntfy:kc-topic", lambda m: True, n0)
    ok("ntfy: neutral, click /saga?tab=jobs, ohne Kanarie", gn is not None and gn[1].get("title") == tv["title"]["needs"] and
       gn[1].get("message") == tv["body"] and gn[1].get("click") == BASE + "/saga?tab=jobs" and CAN_T not in json.dumps(gn[1]),
       gn and gn[1])
    b = [x for x in bell(cA) if x.get("job") == jid]
    ok("Glocke: ein Eintrag, Text t1, Ziel, Persona", len(b) == 1 and b[0]["kind"] == "job.needs_you" and
       b[0]["text"] == f"Wartet auf dich: {CAN_T}" and b[0]["url"] == f"/saga?tab=jobs&job={jid}" and b[0]["title"] == "Marin · KI", b)
    time.sleep(6)
    ok("genau einmal je Weg", len(of("w1", n0, ("job.needs_you",))) == 1 and len(of("u0", n0, ("job.needs_you",))) == 1, len(got))
    n1 = len(got)
    move(jid, "abgebrochen", reason="person")
    gx = wait_for("w1", lambda m: m.get("kind") == "job.changed" and m.get("job") == jid, n1)
    ok("aus wartet_dich abgebrochen: update removed an w1 (caps update)", gx is not None and gx[1].get("update") is True and
       gx[1].get("removed") is True and gx[2].get("Topic") == push.e2e_collapse(b64u(KEYS["w1"][2]), f"ujob-{jid}")[:32], gx and gx[1])
    time.sleep(5)
    ok("kein update an w0 (ohne caps), u0/ntfy (Klartext)", not of("w0", n1, ("job.changed",)) and not of("u0", n1) and
       not of("ntfy:kc-topic", n1))
    ok("Glocke: Eintrag weg", not [x for x in bell(cA) if x.get("job") == jid])
    return jid


def part_done(cA):
    n0 = len(got)
    jid = done_job()
    g = wait_for("w1", lambda m: m.get("job") == jid, n0)
    m = g[1] if g else {}
    d = m.get("detail") or {}
    ok("erledigt -> job.done, Titel neutral", m.get("kind") == "job.done" and m.get("title") == VEC["texts"]["de"]["title"]["feedback"], m)
    ok("detail: t1 Erledigt: Titel, t2 nennt das Ergebnis (Positivkontrolle Kanarie)", d.get("t1") == f"Erledigt: {CAN_T}" and
       CAN_P in (d.get("t2") or "") and d.get("b") == "report", d)
    ok("Knoepfe undo + quiet", [a["a"] for a in m.get("actions") or []] == ["undo", "quiet"], m.get("actions"))
    ok("Urgency normal", g is not None and g[2].get("Urgency") == "normal", g and g[2])
    with connect() as c:
        toks = c.execute("SELECT action, sub_id, plan_v, used_at FROM ai_act_token WHERE job_id=? ORDER BY action", (jid,)).fetchall()
    ok("Token in der DB: je Abo mit actions je Knopf, nur Hash", sorted({r[0] for r in toks}) == ["quiet", "undo"] and
       {r[1] for r in toks} == {SUBS["w1"], SUBS["u1"]} and all(r[3] is None for r in toks), toks)
    b = [x for x in bell(cA) if x.get("job") == jid]
    ok("Glocke: Erledigt: Titel", len(b) == 1 and b[0]["text"] == f"Erledigt: {CAN_T}", b)
    from saga import i18n, notify
    tok = i18n.set_lang("en")
    try:
        with connect() as c:
            b_en = notify.item(c, A, b[0]["id"]) if b else None
    finally:
        i18n._cur.reset(tok)
    ok("Glocke englisch: Done: Titel, Marin · AI", b_en and b_en["text"] == f"Done: {CAN_T}" and b_en["title"] == "Marin · AI", b_en)
    n1 = len(got)
    move(jid, "zurueckgenommen")
    gx = wait_for("w1", lambda m: m.get("kind") == "job.changed" and m.get("job") == jid, n1)
    ok("zurueckgenommen: update removed", gx is not None and gx[1].get("removed") is True, gx and gx[1])
    ok("Glocke nach Ruecknahme leer", not [x for x in bell(cA) if x.get("job") == jid])
    # 60-s-Fenster
    n2 = len(got)
    jq = done_job(quick=True)
    time.sleep(6.5)
    ok("erledigt <= 60 s nach der Freigabe: nur Glocke, kein Push", not [x for x in got[n2:] if x[1].get("job") == jq] and
       [x for x in bell(cA) if x.get("job") == jq])
    # nicht rueckgaengig machbar -> done.plain (nur quiet)
    n3 = len(got)
    jd = done_job(title=CAN_T + " B", steps=[{"tool": "create_task", "args": {}, "undo_class": "delete_only"}])
    g = wait_for("w1", lambda m: m.get("job") == jd, n3)
    ok("delete_only: nur quiet (Kategorie done.plain)", g and [a["a"] for a in g[1].get("actions") or []] == ["quiet"], g and g[1])
    # abgebrochen aus wartet_ok (Herkunft saga): nichts
    n4 = len(got)
    jc = mk()
    ok("abbrechen aus wartet_ok", jobs.cancel(jc, A, "person"))
    time.sleep(6)
    ok("abgebrochen (aus wartet_ok, Herkunft saga): keine Mitteilung, keine Glocke",
       not [x for x in got[n4:] if x[1].get("job") == jc] and not [x for x in bell(cA) if x.get("job") == jc])
    # fehlgeschlagen
    n5 = len(got)
    jf = mk()
    move(jf, "laeuft", "fehlgeschlagen", reason="konflikt")
    g = wait_for("w1", lambda m: m.get("job") == jf, n5)
    m = g[1] if g else {}
    ok("fehlgeschlagen -> job.failed ohne Knoepfe, t1 Hat nicht geklappt", m.get("kind") == "job.failed" and not m.get("actions") and
       (m.get("detail") or {}).get("t1") == f"Hat nicht geklappt: {CAN_T}" and m.get("state") == "failed", m)


def part_outside(cA):
    n0 = len(got)
    jid = mk(source="share", steps=[{"tool": "create_task", "args": {"text": "x"}, "klass": "own", "undo_class": "undo"}],
             due_at=jobs.now() + 7200)
    g = wait_for("w1", lambda m: m.get("job") == jid, n0)
    m = g[1] if g else {}
    ok("Auftrag von aussen (share) -> needs_you mit approve + later", m.get("kind") == "job.needs_you" and
       [a["a"] for a in m.get("actions") or []] == ["approve", "later"] and (m.get("detail") or {}).get("b") == "plan" and
       m["detail"].get("t2") == VEC["texts"]["de"]["t2"]["needs.plan"], m)
    with connect() as c:
        exp = c.execute("SELECT MAX(expires) FROM ai_act_token WHERE job_id=?", (jid,)).fetchone()[0]
    ok("Token 'braucht dich' gilt bis zur Frist", exp is not None and abs(exp - (jobs.now() + 7200)) < 120, exp)
    jobs.cancel(jid, A, "person")
    n1 = len(got)
    j2 = mk(source="api", steps=[{"tool": "create_task", "args": {}}, {"tool": "send_message", "args": {}, "klass": "out",
                                                                       "undo_class": "final"}])
    g = wait_for("w1", lambda m: m.get("job") == j2, n1)
    ok("Plan mit Schritt nach aussen: kein approve", g and [a["a"] for a in g[1].get("actions") or []] == ["later"], g and g[1])
    jobs.cancel(j2, A, "person")


def part_prefs(cA):
    # notify_content never
    s, d = req("PUT", "/api/ai/prefs", cA, {"notify_content": "never"})
    ok("notify_content never gesetzt", s == 200 and d.get("notify_content") == "never", d)
    n0 = len(got)
    jid = done_job()
    g = wait_for("w1", lambda m: m.get("job") == jid, n0)
    ok("never: kein detail, kein undo (nur quiet)", g and "detail" not in g[1] and [a["a"] for a in g[1].get("actions") or []] == ["quiet"],
       g and g[1])
    req("PUT", "/api/ai/prefs", cA, {"notify_content": "unlocked"})
    # Bereich jobs aus
    s, _ = req("PUT", "/api/push/settings", cA, {"cats": {"jobs": False}})
    n1 = len(got)
    j2 = done_job()
    time.sleep(6.5)
    ok(f"cats.jobs aus: kein Push, Glocke ja ({s})", s == 200 and not [x for x in got[n1:] if x[1].get("job") == j2] and
       [x for x in bell(cA) if x.get("job") == j2])
    req("PUT", "/api/push/settings", cA, {"cats": {"jobs": True}})
    # "Gerade nicht"
    ai_saga.set_quiet(A, "push", testclock.now() + 3600)
    s, d = req("GET", "/api/ai/prefs", cA)
    ok("/api/ai/prefs meldet notify_quiet_until", s == 200 and isinstance(d.get("notify_quiet_until"), (int, float)), d)
    n2 = len(got)
    j3 = done_job()
    time.sleep(6.5)
    ok("Gerade nicht: kein Push, Glocke ja", not [x for x in got[n2:] if x[1].get("job") == j3] and [x for x in bell(cA) if x.get("job") == j3])
    s, d = req("PUT", "/api/ai/prefs", cA, {"notify_quiet_until": None})
    ok("Wieder an (notify_quiet_until null)", s == 200 and d.get("notify_quiet_until") is None and ai_saga.quiet_until(A, "push") is None, d)
    s, _ = req("PUT", "/api/ai/prefs", cA, {"notify_quiet_until": "morgen"})
    ok("notify_quiet_until kein Zahlwert -> 400", s == 400)


def part_status(cA):
    # Beschaeftigt ohne Ton -> silent
    req("PUT", "/api/account/status/prefs", cA, {"busy": "silent"})
    s, _ = req("PUT", "/api/account/status", cA, {"state": "busy"})
    n0 = len(got)
    jid = done_job()
    g = wait_for("w1", lambda m: m.get("job") == jid, n0)
    ok(f"Beschaeftigt ohne Ton -> silent ({s})", g is not None and g[1].get("silent") is True, g and g[1])
    # Nicht stoeren -> zurueckgehalten, Sammelmeldung mit jobs
    s, _ = req("PUT", "/api/account/status", cA, {"state": "dnd"})
    n1 = len(got)
    done_job()
    done_job()
    time.sleep(6.5)
    ok(f"Nicht stoeren: keine job.* ({s})", not [x for x in got[n1:] if str(x[1].get("kind", "")).startswith("job.")])
    s, _ = req("PUT", "/api/account/status", cA, {"state": "available"})
    g = wait_for("w1", lambda m: m.get("kind") == "status.digest", n1)
    dg = (g[1].get("digest") if g else None) or {}
    ok(f"Sammelmeldung: jobs 2 (Teil von other), Satz mit Persona ({g and g[1].get('body')})",
       dg.get("jobs") == 2 and dg.get("other", 0) >= 2 and "2 Rückmeldungen von Marin" in g[1].get("body", ""), g and g[1])
    req("PUT", "/api/account/status/prefs", cA, {"busy": "show"})


def part_unit():
    """Ohne Zustellung: push.message() / Planung mit den Haken dieses Prozesses."""
    jid = done_job()
    with connect() as c:
        m_ap = push.message(c, A, "job.done", data={"job": jid}, sub_kind="apns", has_keys=True,
                            sub={"id": None, "kind": "apns", "caps": ("update", "detail", "actions"), "device": None, "lang": "de",
                                 "e2e": False})
        body = push._apns_plain_body(m_ap, "alert") if m_ap else {}
        ok("APNs direkt ohne E2E: Klartext ohne Kennung/detail/actions, thread-id saga-job",
           m_ap and not any(k in m_ap for k in ("job", "detail", "actions", "pv")) and body["aps"]["thread-id"] == "saga-job" and
           body.get("state") == "done" and CAN_T not in json.dumps(body, ensure_ascii=False), body)
        ok("e2e_of: APNs ohne Schluessel nie, Web Push immer, UP nur mit Schluesseln",
           not push.e2e_of("apns") and push.e2e_of("webpush") and not push.e2e_of("unifiedpush") and push.e2e_of("unifiedpush", "a", "b")
           and not push.e2e_of("ntfy") and not push.e2e_of("apns-voip", "a", "b"))
        try:
            push._apns_plan({"kind": "job.done", "tag": "job-1"}, voip=True)
            voip = True
        except ValueError:
            voip = False
        ok("nie VoIP: _apns_plan verweigert job.* fuer VoIP", not voip)
    with connect() as c, c:
        sv = c.execute("INSERT INTO push_sub (uid, kind, endpoint, name, created) VALUES (?, 'apns-voip', ?, 'voip', ?)",
                       (A, "apns-voip:production:" + "ab" * 32, time.time())).lastrowid
        routed = push._route(c, [sv, SUBS["w1"]], "job.done")
        c.execute("DELETE FROM push_sub WHERE id=?", (sv,))
    ok(f"VoIP-Abo bekommt keine job.* (_route: {routed})", sv not in routed and SUBS["w1"] in routed)
    with connect() as c:
        big = {"title": "t", "body": "b", "kind": "job.done", "detail": {"b": "report", "f": {"x": "ฟ" * 300}, "lang": "th",
                                                                          "t1": "t1", "t2": "ข" * 900}}
        ai_notify.fit(big)
        raw = json.dumps(big, ensure_ascii=False, separators=(",", ":")).encode()
        ok(f"Groesse: erst detail.f weg, dann t2 gekuerzt ({len(raw)} Byte)", "f" not in big["detail"] and len(raw) <= 2400 and
           big["detail"]["t2"].endswith("…") and big["detail"]["t1"] == "t1", len(raw))
    # Groesse (Thai)
    th = "รายการซื้อของ สำหรับงานเลี้ยงปิ้งย่าง ของครอบครัว ที่สวนหลังบ้าน ในวันเสาร์นี้ตอนเย็น พร้อมถ่านและน้ำแข็ง " * 4
    j2 = mk(title=th)
    for i in range(5):
        change(j2, text=th + str(i))
    now = jobs.now()
    move(j2, "laeuft", "erledigt", at=[now - 120, now])
    with connect() as c:
        m = push.message(c, A, "job.done", data={"job": j2}, sub_kind="webpush", lang="th",
                         sub={"id": None, "kind": "webpush", "caps": ("detail", "actions"), "device": None, "lang": "th", "e2e": True})
    raw = json.dumps(m, ensure_ascii=False, separators=(",", ":")).encode()
    ok(f"Thai: innerer Text <= 2400 Byte ({len(raw)}), t1 gekuerzt, Thai-Text", len(raw) <= 2400 and
       ai_say.graphemes(m["detail"]["t1"]) <= 80 and m["detail"]["t1"].startswith("เสร็จแล้ว") and m["lang"] == "th", m.get("detail"))
    ok("Thai: t2 <= 100 Grapheme", ai_say.graphemes(m["detail"].get("t2") or "") <= 100, m["detail"].get("t2"))


def part_canaries(cA):
    blob_plain = json.dumps([g[1] for g in got if g[0] in ("u0", "ntfy:kc-topic")], ensure_ascii=False) + \
        "".join(g[4].decode("utf-8", "replace") for g in got if g[0] in ("u0", "ntfy:kc-topic"))
    ok("Kanarien nie auf Klartext-Wegen (ntfy, UnifiedPush ohne Schluessel)", all(x not in blob_plain for x in (CAN_T, CAN_P, CAN_N)),
       [x for x in (CAN_T, CAN_P, CAN_N) if x in blob_plain])
    heads = json.dumps([g[2] for g in got], ensure_ascii=False)
    ok("Kanarien nie in Koepfen (Topic)", all(x not in heads for x in (CAN_T, CAN_P, CAN_N)))
    enc = [g for g in got if g[0] == "w1" and (g[1].get("detail") or {}).get("t2") and CAN_P in g[1]["detail"]["t2"]]
    ok("Positivkontrolle: Kanarie im entschluesselten detail", bool(enc))
    with connect() as c:
        q = json.dumps(c.execute("SELECT data FROM push_queue WHERE uid=?", (A,)).fetchall())
        tk = json.dumps(c.execute("SELECT * FROM ai_act_token WHERE uid=?", (A,)).fetchall(), default=str)
    ok("Kanarien nie in push_queue.data und ai_act_token", all(x not in q + tk for x in (CAN_T, CAN_P, CAN_N)))
    s, d = req("GET", "/api/notifications?limit=50", cA)
    ok("Positivkontrolle: Kanarie (Titel) in der Glocke", CAN_T in json.dumps(d, ensure_ascii=False))
    lp = os.environ.get("HK_TEST_LOG")
    if lp and os.path.exists(lp):
        log = open(lp, encoding="utf-8", errors="replace").read()
        ok("Kanarien nie im Server-Log", all(x not in log for x in (CAN_T, CAN_P, CAN_N)),
           [x for x in (CAN_T, CAN_P, CAN_N) if x in log])


def cleanup():
    with connect() as c, c:
        c.execute("DELETE FROM job WHERE uid=?", (A,))
        c.execute("DELETE FROM task WHERE uid=?", (A,))
        c.execute("DELETE FROM notification WHERE uid=?", (A,))
        c.execute("DELETE FROM push_queue WHERE uid=?", (A,))
        c.execute("DELETE FROM push_sub WHERE uid=?", (A,))
        c.execute("DELETE FROM user_kv WHERE uid=? AND k IN ('ai_quiet', 'status_held', 'push')", (A,))
        c.execute("DELETE FROM user_status WHERE uid=?", (A,))


def main():
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("push", "allowed_hosts", "ai", "ai_persona", ai_plan.SETTING)}
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", FP), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        auth.save_setting("push", {"enabled": True, "webpush": True, "ntfy": True, "unifiedpush": True})
        auth.save_setting("allowed_hosts", ", ".join(x for x in (prev.get("allowed_hosts") or "", f"127.0.0.1:{FP}") if x))
        auth.save_setting("ai", dict(prev.get("ai") or {}, enabled=True, provider="openai", url="http://127.0.0.1:9/v1", model="x",
                                     pinned=["127.0.0.1"], groups=[], caps={}))
        auth.save_setting("ai_persona", dict(prev.get("ai_persona") or {}, name="Marin"))
        auth.save_setting(ai_plan.SETTING, {"level": "plans"})
        if not auth.get_user(A):
            auth.create_user(A, PW, "Anna Push")
        set_user_lang(A, "de")
        cleanup()
        auth.settings(fresh=True)
        ai_saga.grant(A)
        time.sleep(5.6)                   # Einstellungen sind im Server 5 s zwischengespeichert
        cA = ck(A)
        subscribe(cA)
        part_api(cA)
        part_needs(cA)
        part_done(cA)
        part_outside(cA)
        part_prefs(cA)
        part_status(cA)
        part_unit()
        part_canaries(cA)
    finally:
        cleanup()
        for k, v in keep.items():
            if v is None:
                with connect() as c, c:
                    c.execute("DELETE FROM setting WHERE k=?", (k,))
            else:
                auth.save_setting(k, v)
        srv.shutdown()
    ok("Keine Serverfehler (HTTP 5xx)", n5xx[0] == 0, n5xx[0])
    good = sum(res)
    print(f"KOLLEGIN-PUSH {good}/{len(res)} - " + ("ALLES GRUEN" if good == len(res) else "FEHLER"), flush=True)
    sys.exit(0 if good == len(res) else 1)


if __name__ == "__main__":
    _ = ai_notify
    main()
