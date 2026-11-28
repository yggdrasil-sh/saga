"""Mitteilungen der Kollegin zu Auftraegen (Saga 2.6, Bugs-DB 2270 K-C, Konzept K14/K15; Vertrag docs/APP-API.md
"Push-Nutzlast 3 und Auftraege (jobpush1)", docs/APP-API-KI.md Abschnitt 12, Vektoren tests/vectors/push_kollegin.json).

Hoert auf "job.state" (saga.jobs._to) und "job.created" (saga.jobs.create), nur Auftraege der Kollegin (ai_plan.KIND), und
arbeitet NACH dem Commit in einer eigenen Transaktion (keine neue Sperrreihenfolge in jobs):
  -> wartet_dich                 Glocke + Push job.needs_you
  -> wartet_ok (Herkunft != saga) Glocke + Push job.needs_you (auch beim Anlegen)
  -> erledigt                    Glocke + Push job.done - nicht <= 60 s nach der Freigabe (die Person sieht es gerade)
  -> fehlgeschlagen              Glocke + Push job.failed
  sonst (abgebrochen, zurueckgenommen, aus wartet_dich/wartet_ok heraus): Glocken-Eintraege des Auftrags weg und - wenn
  es einen gab - stilles "update removed" (push.JOB_UPDATE) an Abos mit caps update.
Doppelt-Schutz: der Glocken-Eintrag merkt sich die letzte job_event-id (ev); derselbe Stand meldet sich nie zweimal.

Beim Zustellen baut build() (Haken push.JOB_MESSAGE) je Abo die Nutzlast 3: neutraler title/body auf allen Wegen,
Kennungen, detail (t1/t2) und Knoepfe mit Einmal-Token (saga.ai_act) nur auf echten E2E-Wegen (push.e2e_of) und nur mit
Abo-caps detail/actions; detail nie bei notify_content "never"; approve/undo nur mit detail. Glocke: notify.RENDER."""
import time

from db import connect as _db
from saga import ai_act, ai_plan, ai_saga, ai_say, bus, notify, push
from saga.core import t

KIND = ai_plan.KIND
GROUP_OF = {"wartet_dich": "needs", "wartet_ok": "needs", "erledigt": "done", "fehlgeschlagen": "failed"}
KIND_OF = {"needs": "job.needs_you", "done": "job.done", "failed": "job.failed"}
GROUP_OF_KIND = {v: k for k, v in KIND_OF.items()}
DONE_WINDOW = 60                     # s: erledigt so kurz nach "Passt, los" -> nur Glocke
T1_MAX, T2_MAX = 60, 100             # Grapheme (Titel in t1; Satz t2 = Kanal push von saga.ai_say)
LATER_S, LATER_MAX = 3 * 3600, 2     # "Spaeter": nach 3 h erneut, hoechstens 2x
ACT_TTL, ACT_MAX = 86400, 14 * 86400


def persona_name():
    from saga import ai
    return ai.persona_name()


def head(group):
    """Neutraler title/body (Sprache der Anfrage) - ohne Auftragstitel und ohne Namen von Personen (M5, RT-30)."""
    nm = persona_name()
    title = t("{name} · KI – braucht dich", name=nm) if group == "needs" else t("{name} · KI – Rückmeldung", name=nm)
    return title, t("In Saga ansehen")


def t1_text(group, title):
    s = ai_say._sv(title or "", T1_MAX) or t("Auftrag")
    if group == "done":
        return t("Erledigt: {text}", text=s)
    if group == "failed":
        return t("Hat nicht geklappt: {text}", text=s)
    return t("Wartet auf dich: {text}", text=s)


def texts(c, uid, job_id, state, title, lang):
    """-> (b, f, t1, t2) in lang - ohne Modell, ohne Zitate (report_from_log + Bausteine, Kanal push)."""
    group = GROUP_OF[state]
    t1 = t1_text(group, title)
    if state == "wartet_ok":
        n = c.execute("SELECT COUNT(*) FROM job_step WHERE job_id=?", (job_id,)).fetchone()[0]
        return "plan", {"n": n}, t1, t("Der Plan steht und wartet auf ein OK.")
    p = ai_say.persona_for(uid, c)
    zug, f, _ = ai_say.report_from_log(c, job_id, lang, p)
    if not zug:
        return None, None, t1, ""
    t2 = ai_say.say(zug, dict(f, channel="push"), lang, p)
    return zug, f, t1, ai_say.cut(t2, T2_MAX, lang)


def url_of(job_id):
    return f"/saga?tab=jobs&job={int(job_id)}"


# --- Glocke ------------------------------------------------------------------------------------------------------
def _bell_rows(c, uid, job_id):
    out = []
    for rid, kind, raw in c.execute(f"SELECT id, kind, data FROM notification WHERE uid=? AND kind IN ({','.join('?' * len(KIND_OF))})",
                                    (uid, *KIND_OF.values())).fetchall():
        d = push._json_load(raw, {})
        if d.get("job") == job_id:
            out.append((rid, kind, d))
    return out


def _drop_bell(c, uid, job_id):
    """Glocken-Eintraege des Auftrags entfernen (Live notification.changed removed) -> Anzahl."""
    rows = _bell_rows(c, uid, job_id)
    for rid, _kind, _d in rows:
        c.execute("DELETE FROM notification WHERE id=?", (rid,))
        bus.emit_after(c, "notification.changed", {"uid": uid, "id": rid, "removed": True, "group_key": None})
    return len(rows)


def _render(c, uid, item, d):
    """notify.RENDER fuer job.*: Text = t1 (jetziger Titel), Ziel = Auftrag im Ort "Saga"."""
    jid = d.get("job")
    r = c.execute("SELECT title FROM job WHERE id=? AND uid=? AND kind=?", (jid, uid, KIND)).fetchone() if isinstance(jid, int) else None
    item["system"] = True
    item["title"] = t("{name} · KI", name=persona_name())
    if not r:
        item.update(access=False, url=None, text=t("Diesen Auftrag gibt es nicht mehr."))
        return
    item.update(access=True, job=jid, url=url_of(jid), text=t1_text(GROUP_OF_KIND.get(item["kind"], "done"), r[0]))


for _k in KIND_OF.values():
    notify.RENDER[_k] = _render


# --- Uebergaenge -------------------------------------------------------------------------------------------------
# Herkunft, bei der die Person den Plan gerade selbst vor sich hat: kein "braucht dich" fuer wartet_ok (Ort "Saga" und
# K-G 2275 Teilen - die App zeigt die Plan-Karte sofort, F8); geteilte Auftraege bekommen nie einen Knopf approve (K17).
LOCAL_SOURCES = ("saga", "share")


def _approved_at(c, job_id):
    r = c.execute("SELECT MAX(at) FROM job_event WHERE job_id=? AND kind='approve'", (job_id,)).fetchone()
    if r and r[0] is not None:
        return r[0]
    r = c.execute("SELECT MIN(at) FROM job_event WHERE job_id=? AND kind='state' AND data->>'to'='laeuft'", (job_id,)).fetchone()
    return r[0] if r else None


def handle(job_id, uid, old, new):
    """Nach dem Commit eines Wechsels (bzw. des Anlegens, old None): Glocke und Push in eigener Transaktion."""
    from saga import jobs
    with _db() as c, c:
        j = c.execute("SELECT state, source FROM job WHERE id=? AND uid=? AND kind=?", (job_id, uid, KIND)).fetchone()
        if not j or j[0] != new:
            return                        # schon weiter (der naechste Wechsel meldet sich selbst) bzw. geloescht
        group = GROUP_OF.get(new)
        if group is None or (new == "wartet_ok" and j[1] in LOCAL_SOURCES):
            if _drop_bell(c, uid, job_id):
                push.enqueue_job_update(c, uid, job_id)
            else:
                push.drop_job(c, uid, job_id)
            return
        ev = (c.execute("SELECT MAX(id) FROM job_event WHERE job_id=?", (job_id,)).fetchone() or [None])[0]
        old_rows = _bell_rows(c, uid, job_id)
        if any(d.get("ev") == ev and d.get("state") == group for _r, _k, d in old_rows):
            return                        # derselbe Stand schon gemeldet
        _drop_bell(c, uid, job_id)
        kind = KIND_OF[group]
        nid = notify.create(c, uid, kind, data={"job": job_id, "state": group, "ev": ev})
        if group == "done":
            at = _approved_at(c, job_id)
            if at is not None and jobs.now() - at <= DONE_WINDOW:
                push.drop_job(c, uid, job_id)
                return                    # gerade freigegeben - die Person sieht die Rueckmeldung (nur Glocke)
        push.enqueue(c, uid, kind, ref=nid, data={"job": job_id})


def _safe(fn, *a):
    try:
        fn(*a)
    except Exception as ex:  # noqa: BLE001 - eine Mitteilung darf einen Auftrag nie aufhalten
        print(f"ai_notify: {type(ex).__name__}: {ex}", flush=True)


def _on_state(c, job_id=None, uid=None, kind=None, old=None, new=None, **_):
    if kind == KIND and job_id is not None and uid:
        c.after_commit(lambda: _safe(handle, job_id, uid, old, new))


def _on_created(c, job_id=None, uid=None, kind=None, source=None, **_):
    if kind == KIND and job_id is not None and uid and source not in LOCAL_SOURCES:
        c.after_commit(lambda: _safe(handle, job_id, uid, None, "wartet_ok"))


bus.on("job.state", _on_state)
bus.on("job.created", _on_created)


# --- Nutzlast beim Zustellen (push.JOB_MESSAGE) ----------------------------------------------------------------------
def _undoable(c, job_id, steps):
    if not steps or any(k not in ("read", "own") or cl for k, _u, cl in steps):
        return False
    own = [u for k, u, _cl in steps if k == "own"]
    if not own or any(u != "undo" for u in own):
        return False
    return bool(c.execute("SELECT 1 FROM job_change WHERE job_id=? AND undone_at IS NULL LIMIT 1", (job_id,)).fetchone())


def _approvable(steps):
    return bool(steps) and all(k in ("read", "own") and not cl for k, _u, cl in steps) and \
        all(u == "undo" for k, u, _cl in steps if k == "own")


def actions_for(c, job, steps, detail, again=0):
    """Knoepfe (ohne open) je Zustand: approve/undo nur mit detail, later hoechstens LATER_MAX-mal."""
    state = job["state"]
    acts = []
    if state == "wartet_ok":
        if detail and _approvable(steps) and job.get("source") != "share":    # K-G (2275): Geteiltes nie aus der Mitteilung freigeben
            acts.append("approve")
        if again < LATER_MAX:
            acts.append("later")
    elif state == "wartet_dich":
        if again < LATER_MAX:
            acts.append("later")
    elif state == "erledigt":
        if detail and job["undo_at"] is None and _undoable(c, job["id"], steps):
            acts.append("undo")
        acts.append("quiet")
    return acts


def fit(msg):
    """Innerer Text hoechstens push.E2E_MAX Byte: erst detail.f weg, dann t2 kuerzen."""
    import json

    def size():
        return len(json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode())
    d = msg.get("detail")
    if d and size() > push.E2E_MAX:
        d.pop("f", None)
    while d and size() > push.E2E_MAX and d.get("t2"):
        d["t2"] = ai_say.cut_graphemes(d["t2"], max(0, ai_say.graphemes(d["t2"]) - 2)) + "…"
        if ai_say.graphemes(d["t2"]) <= 1:
            d["t2"] = ""
    if d and size() > push.E2E_MAX:
        msg.pop("detail")
    return msg


def build(c, uid, kind, data, sub, lang, created, silent):
    jid = data.get("job")
    if not isinstance(jid, int) or isinstance(jid, bool):
        return None
    r = c.execute("SELECT id, state, source, title, plan_v, plan_hash, due_at, undo_at FROM job WHERE id=? AND uid=? AND kind=?",
                  (jid, uid, KIND)).fetchone()
    if not r:
        return None
    job = dict(zip(("id", "state", "source", "title", "plan_v", "plan_hash", "due_at", "undo_at"), r))
    e2e, caps = bool(sub.get("e2e")), tuple(sub.get("caps") or ())
    ts = int(created or time.time())
    if kind == push.JOB_UPDATE:
        if not e2e:
            return None                   # Klartext-Wege: keine Kennung, also auch kein gezieltes Entfernen
        return {"title": "", "body": "", "url": url_of(jid), "tag": f"job-{jid}", "kind": kind, "ts": ts, "v": push.PAYLOAD_JOB,
                "lang": lang, "group_key": f"job:{jid}", "job": jid, "update": True, "removed": True}
    group = GROUP_OF.get(job["state"])
    if group is None or KIND_OF[group] != kind or (job["state"] == "wartet_ok" and job["source"] in LOCAL_SOURCES):
        return None                       # Zustand inzwischen anders - nichts Veraltetes zeigen
    title, body = head(group)
    msg = {"title": title, "body": body, "url": url_of(jid) if e2e else "/saga?tab=jobs", "tag": f"job-{jid}" if e2e else "saga-job",
           "kind": kind, "ts": ts, "v": push.PAYLOAD_JOB, "lang": lang}
    if e2e:
        msg.update({"group_key": f"job:{jid}", "thread": f"job-{jid}", "job": jid})
    msg["state"] = group
    if e2e:
        msg["pv"] = job["plan_v"]
    if silent:
        msg["silent"] = True
    detail = e2e and "detail" in caps and ai_saga.notify_content(uid, c) != "never"
    if detail:
        b, f, t1, t2 = texts(c, uid, jid, job["state"], job["title"], lang)
        d = {"b": b} if b else {}
        if f is not None and b:
            d["f"] = f
        d.update({"lang": lang, "t1": t1, "t2": t2})
        msg["detail"] = d
        fit(msg)
        detail = "detail" in msg
    if e2e and "actions" in caps:
        steps = [(k, u, cl) for k, u, cl in c.execute("SELECT klass, undo_class, client FROM job_step WHERE job_id=? ORDER BY n",
                                                      (jid,)).fetchall()]
        again = data.get("again") if isinstance(data.get("again"), int) else 0
        acts = actions_for(c, job, steps, detail, again)
        if acts:
            now = ai_act.now()
            exp = now + ACT_TTL
            if group == "needs" and job["due_at"]:
                exp = max(now + 60, float(job["due_at"]))
            exp = min(exp, now + ACT_MAX)
            toks = ai_act.issue(uid, jid, sub.get("id"), sub.get("device"), job["plan_v"], job["plan_hash"], acts, exp, again)
            msg["actions"] = [{"a": a, "t": toks[a]} for a in acts]
    return msg


def _quiet(c, uid, kind):
    return ai_saga.quiet_until(uid, "push", c) is not None


def register():
    """Haken in saga.push setzen (schon beim Import; app.py ruft es ausdruecklich)."""
    push.JOB_MESSAGE = build
    push.SUPPRESS["jobs"] = _quiet
    push.PERSONA = persona_name


register()
