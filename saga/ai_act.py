"""Knoepfe in Mitteilungen der Kollegin: Einmal-Token und POST /api/ai/jobs/{id}/act (Saga 2.6, Bugs-DB 2270 K-C, Konzept
K15; Vertrag docs/APP-API-KI.md Abschnitt 12, Vektoren tests/vectors/push_kollegin.json "tokens").

Token (atok): secrets.token_urlsafe(18) (24 Zeichen), in der Datenbank nur sha256 (ai_act_token), einmalig, gebunden an
Konto, Auftrag, Abo/Geraet, plan_v + plan_hash, Aktion und Ablauf. Ausgestellt beim Zustellen (saga.ai_notify.build) in
einer eigenen, sofort festgeschriebenen Transaktion (die Zustellung selbst laeuft ohne Transaktion, push.run_once).

act() prueft in fester Reihenfolge (nur 200 verbraucht den Token, unter FOR UPDATE - zweimal gleichzeitig = einmal):
  Schluessel (403 key) -> Auftrag des Kontos (404) -> Faehigkeit ai (403 cap) -> reserviert/open -> Token (410 gone|used|
  expired) -> Geraet (403 device) -> pv/Planstand (409 stale) -> Zustand (409 state) -> Aktion (409 changed|open) -> 200.
Zeit aus testclock (jobs.now)."""
import hashlib
import secrets
import time

from db import connect as _db
from saga import ai_plan, ai_saga, ai_undo, caps, jobs, push, testclock
from saga.core import t

ACTIONS = ("approve", "undo", "later", "quiet", "open")
RESERVED = ("retry", "reply", "ideas_off")
REST = ("later", "quiet")
LATER_S = 3 * 3600
LATER_MAX = 2
QUIET_HOUR = 7
UNDO_WAIT = 3.0
KEEP = 86400                         # s: abgelaufene/benutzte Token so lange behalten (dann jobs.cleanup)


def now():
    return testclock.now()


def tok_hash(tok):
    return hashlib.sha256(str(tok).encode()).hexdigest()


def issue(uid, job_id, sub_id, device, plan_v, plan_hash, actions, expires, again=0):
    """Je Aktion ein Token -> {aktion: token}. Eigene Transaktion (sofort fest)."""
    out, n = {}, now()
    with _db() as c, c:
        for a in actions:
            tok = secrets.token_urlsafe(18)
            c.execute("INSERT INTO ai_act_token (hash, uid, job_id, sub_id, device, plan_v, plan_hash, action, again, expires, created) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (tok_hash(tok), uid, job_id, sub_id, device, plan_v, plan_hash or "", a, int(again or 0), float(expires), n))
            out[a] = tok
    return out


def cleanup():
    with _db() as c, c:
        return c.execute("DELETE FROM ai_act_token WHERE expires < ? OR used_at < ?", (now() - KEEP, now() - KEEP)).rowcount


def _err(status, code, text, job_id=None, **more):
    body = {"error": text, **({"code": code} if code else {}), **more}
    if status == 409 and job_id is not None:
        body["url"] = f"/saga?tab=jobs&job={int(job_id)}"
    return status, body


def _hint():
    return t("Der Knopf gilt nicht mehr – bitte in Saga ansehen.")


def _dictated(plan):
    pj = plan if isinstance(plan, dict) else {}
    return isinstance(pj.get("origin"), dict) and pj["origin"].get("kind") == "dictation"


def act(uid, key, device, job_id, action, atok, pv):
    """key: "session" | "hkd" | "hk"; device: Geraete-Anmeldung des Aufrufs (oder None). -> (status, body)."""
    if key == "hk" and action not in REST:
        return _err(403, "key", t("Das geht nur in der App."))
    with _db() as c:
        j = c.execute("SELECT state, plan_v, plan_hash, undo_at, source, plan FROM job WHERE id=? AND uid=? AND kind=?",
                      (job_id, uid, ai_plan.KIND)).fetchone()
    if not j:
        return 404, {"error": "not found"}
    if not caps.can(uid, "ai"):
        return _err(403, "cap", t("Der KI-Assistent ist für dich nicht eingeschaltet."))
    if action in RESERVED or action not in ACTIONS:
        return _err(409, "open", t("Das geht nur in Saga selbst."), job_id)
    if action == "open":
        return 200, {"ok": True, "action": "open", "url": f"/saga?tab=jobs&job={job_id}"}
    if action == "approve" and j[4] == "share":       # K-G (2275, K17/F8): Geteiltes nur ueber die Plan-Karte freigeben
        return _err(409, "open", t("Das geht nur in Saga selbst."), job_id)
    if action == "approve" and _dictated(j[5]):       # K-F (2274, RT-18): Diktiertes ebenso (Fernseher im Raum)
        return _err(409, "open", t("Das geht nur in Saga selbst."), job_id)
    if not isinstance(atok, str) or not 16 <= len(atok) <= 64:
        return _err(410, "gone", _hint())
    with _db() as c, c:
        r = c.execute("SELECT uid, job_id, sub_id, device, plan_v, plan_hash, action, again, expires, used_at FROM ai_act_token "
                      "WHERE hash=? FOR UPDATE", (tok_hash(atok),)).fetchone()
        if not r or r[0] != uid or r[1] != job_id or r[6] != action:
            return _err(410, "gone", _hint())
        if r[9] is not None:
            return _err(410, "used", _hint())
        if r[8] < now():
            return _err(410, "expired", _hint())
        if key != "hk":
            if r[3] is not None and device != r[3]:
                return _err(403, "device", t("Dieser Knopf gehört zu einem anderen Gerät."))
            if r[3] is None and key != "session":
                return _err(403, "device", t("Dieser Knopf gehört zu einem anderen Gerät."))
        if pv != r[4] or j[1] != r[4] or j[2] != r[5]:
            return _err(409, "stale", t("Der Plan hat sich inzwischen geändert – bitte noch einmal ansehen."), job_id, plan_v=j[1])
        st, body = _run(c, uid, job_id, action, j, r[7])
        if st == 200:
            c.execute("UPDATE ai_act_token SET used_at=? WHERE hash=?", (now(), tok_hash(atok)))
        else:
            c.rollback()
        return st, body


def _run(c, uid, job_id, action, j, again):
    state = j[0]
    if action == "approve":
        if state != "wartet_ok":
            return _err(409, "state", t("Dieser Auftrag wartet nicht mehr auf dein OK."), job_id, state=state)
        steps = c.execute("SELECT klass, undo_class, client FROM job_step WHERE job_id=?", (job_id,)).fetchall()
        if not steps or any(k not in ("read", "own") or cl or (k == "own" and u != "undo") for k, u, cl in steps):
            return _err(409, "open", t("Das geht nur in Saga selbst."), job_id)
        if not ai_saga.consent_ok(uid):
            return _err(409, "open", t("Das geht nur in Saga selbst."), job_id)
        if not jobs.approve(job_id, uid, j[1], j[2], dev=t("Mitteilung")):
            return _err(409, "state", t("Dieser Auftrag wartet nicht mehr auf dein OK."), job_id)
        return 200, {"ok": True, "action": "approve", "state": "laeuft"}
    if action == "undo":
        st, out = undo_whole(uid, job_id)
        if st != 200:
            return st, dict(out, **({"url": f"/saga?tab=jobs&job={job_id}"} if st == 409 else {}))
        return 200, {"ok": True, "action": "undo", "state": out.get("state")}
    if action == "later":
        if state not in ("wartet_ok", "wartet_dich") or again >= LATER_MAX:
            return _err(409, "state", t("Das lässt sich gerade nicht verschieben."), job_id, state=state)
        push.enqueue(c, uid, "job.needs_you", data={"job": job_id, "again": int(again) + 1}, delay=LATER_S, later=True)
        return 200, {"ok": True, "action": "later", "state": state, "at": int(now() + LATER_S)}
    if action == "quiet":
        tz = (push.prefs(c, uid).get("quiet") or {}).get("tz") or None
        until = ai_saga.set_quiet(uid, "push", ai_saga.next_local(QUIET_HOUR, tz), c)
        push.drop_job(c, uid, None)
        return 200, {"ok": True, "action": "quiet", "state": state, "until": int(until) if until else None}
    return _err(409, "open", t("Das geht nur in Saga selbst."), job_id)


def undo_whole(uid, job_id):
    """Den ganzen Auftrag zuruecknehmen (gemeinsam mit POST /api/ai/jobs/{id}/undo) -> (status, body); wartet bis
    UNDO_WAIT Sekunden auf den Worker. 409 code state|changed wie dort."""
    from saga.ai_jobs import state_text
    with _db() as c:
        r = c.execute("SELECT state, undo_at FROM job WHERE id=? AND uid=? AND kind=?", (job_id, uid, ai_plan.KIND)).fetchone()
    if not r:
        return 404, {"error": "not found"}
    if r[0] != "erledigt" or r[1] is not None:
        return 409, {"error": t("Das lässt sich gerade nicht zurücknehmen."), "code": "state", "state": r[0]}
    bad = ai_undo.conflicts(job_id, uid)
    if bad:
        return 409, {"error": t("Das wurde seitdem geändert."), "code": "changed", "changes": bad,
                     "version_url": next((x["version_url"] for x in bad if x["why"] == "changed" and x["version_url"]), None)}
    if not jobs.undo(job_id, uid):
        return 409, {"error": t("Das lässt sich gerade nicht zurücknehmen."), "code": "state", "state": r[0]}
    t0 = time.monotonic()
    s = None
    while time.monotonic() - t0 < UNDO_WAIT:
        s = jobs.state_of(job_id)
        if s and s["state"] != "erledigt":
            break
        time.sleep(0.1)
    st = (s or {}).get("state")
    return 200, {"ok": True, "state": st, "state_text": state_text(st)}
