"""Auftraege der Kollegin - HTTP (Saga 2.6, Bugs-DB 2267 K-B, Architektur §3, docs/APP-API-KI.md Abschnitt "Auftraege").

Einen Pfad je Endpunkt, nur unter /api/ai/jobs/... (damit die Schluesselregeln ueberall greifen):
  POST /api/ai/jobs                      Auftrag geben (SSE): status, dann plan | ask | refuse | error
  GET  /api/ai/jobs[?group=]             Liste in 4 Gruppen (K2) - auch mit Widget-Schluessel hk_ (nur das)
  GET  /api/ai/jobs/{id}                 Detail: genaues Zustandswort, Plan, Aenderungen, Rueckmeldung, Protokoll
  PUT  /api/ai/jobs/{id}/plan            "Aendern": {plan_v, edits: [{n, args}]} -> neuer plan_v/plan_hash
  POST /api/ai/jobs/{id}/approve         "Passt, los": {plan_v, plan_hash} (sig kommt mit K-E, Schritte nach aussen)
  POST /api/ai/jobs/{id}/cancel          "Lieber nicht" / "Auftrag abbrechen"
  POST /api/ai/jobs/{id}/undo            Rueckgaengig: {change?} - Konflikt 409 {code: changed, version_url}
  GET  /api/ai/jobs/{id}/changes/{c}/before   "alte Fassung holen"
Schluessel: Sitzung (Web, Apps nach /app/resume mit hkd_) mit Kopf X-Saga: 1; hk_ (Widget) nur GET der Liste;
jeder andere Bearer-Schluessel (sga_ API/MCP, OAuth) -> 403, nie 2xx (RT-42) - auch fuer Pfade, die es (noch) nicht gibt
(act, outbox, inbox: K-C/K-E). Rechte prueft immer der Server (caps.can "ai"; Auftraege geben nur mit Stufe != answers).
Live: ai.job.changed {id, state, sub, plan_v} (saga.jobs). Zustandswoerter/Kennzeichnungen = Systemtext (i18n)."""
import asyncio
import contextvars
import json
import threading
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from db import connect as _db
from saga import ai, ai_plan, ai_undo, caps, jobs
from saga.core import _api_guard, _bad, _body, t

router = APIRouter()
NO_STORE = {"Cache-Control": "no-store"}
LIST_MAX = 200
DONE_DAYS = 30                       # "Fertig" zeigt Auftraege der letzten 30 Tage
GROUP_ORDER = ("wartet_auf_dich", "laeuft", "fertig", "nicht_geklappt")
UNDO_WAIT = 3.0                      # s: so lange wartet undo auf den Worker (danach Antwort mit dem Zwischenstand)


def state_text(st):
    return {"wartet_ok": t("Wartet auf dein OK"), "laeuft": t("Läuft"), "wartet_dich": t("Wartet auf dich"), "erledigt": t("Erledigt"),
            "fehlgeschlagen": t("Hat nicht geklappt"), "abgebrochen": t("Abgebrochen"), "zurueckgenommen": t("Zurückgenommen")}.get(st, st)


def group_text(g):
    return {"wartet_auf_dich": t("Wartet auf dich"), "laeuft": t("Läuft"), "fertig": t("Fertig"),
            "nicht_geklappt": t("Hat nicht geklappt")}.get(g, g)


def reason_text(state, sub, reason):
    """Grund in Klartext (Systemtext, UX R6) oder ''."""
    name = ai.persona_name()
    if state == "laeuft" and sub == "wartet_modell":
        return t("{name} ist gerade nicht erreichbar. Dein Auftrag wartet.", name=name)
    if state == "laeuft" and reason == "budget":
        return t("zurückgestellt bis morgen")
    if state == "laeuft":
        return ""
    r = {"kein_zugriff": t("Darauf darf ich nicht mehr zugreifen – nichts wurde geändert."),
         "konflikt": t("Das Ziel wurde inzwischen geändert – nichts wurde überschrieben."),
         "abgemeldet": t("Du hast dich inzwischen abgemeldet – bitte gib den Auftrag neu."),
         "ai": t("Die KI ist für dich gerade nicht eingeschaltet."), "ai_act": t("Dafür fehlt dir die Berechtigung."),
         "einwilligung": t("Du hast die Einwilligung zurückgenommen – der Auftrag ruht."),
         "konto_gesperrt": t("Das Konto ist gesperrt."), "konto_weg": t("Das Konto gibt es nicht mehr."),
         "modell": t("{name} war zu lange nicht erreichbar.", name=name),
         "plan_geaendert": t("Der Plan wurde verändert – nichts wurde ausgeführt."),
         "abgelaufen": t("Nicht freigegeben – der Vorschlag ist verfallen."),
         "ungueltig": t("Das hat nicht geklappt – die Angaben passten nicht."),
         "werkzeug": t("Das hat nicht geklappt."), "intern": t("Das hat nicht geklappt."), "versuche": t("Das hat nicht geklappt."),
         "person": ""}.get(reason or "", "")
    if state == "abgebrochen" and not r:
        return t("Abgebrochen – nichts ist passiert.")
    return r


# --- Schluessel ---------------------------------------------------------------------------------------------------
def _who(request, widget=False):
    """-> (uid, fehlerantwort). Bearer: nur hk_ und nur, wenn widget (GET der Liste); alles andere 403 (RT-42)."""
    hdr = request.headers.get("authorization") or ""
    if hdr.strip():
        if widget and hdr.startswith("Bearer hk_"):
            from saga.app_api import _app_uid
            uid = _app_uid(request)
            return (uid, None) if uid else (None, _bad("unauthorized", 401))
        return None, _bad(t("Aufträge gibt es nur in Saga selbst – nicht über API-Schlüssel."), 403)
    uid = _api_guard(request)
    if not uid:
        return None, _bad("unauthorized", 401)
    return uid, None


def _jid(v):
    try:
        i = int(v)
    except (TypeError, ValueError):
        return None
    return i if 0 < i < 2 ** 63 else None


def _allowed(uid):
    return caps.can(uid, "ai")


# --- Liste und Detail ---------------------------------------------------------------------------------------------
def _line(state, sub, reason, steps_open):
    rt = reason_text(state, sub, reason)
    if rt:
        return rt
    if state == "wartet_ok":
        return t("Wartet auf dein OK")
    if state == "laeuft":
        return t("läuft")
    return ""


def list_jobs(uid, group=None):
    since = jobs.now() - DONE_DAYS * 86400
    with _db() as c:
        rows = c.execute("SELECT id, title, state, sub, reason, created, updated, plan_v FROM job WHERE uid=? AND kind=? AND "
                         "(ended_at IS NULL OR ended_at > ?) ORDER BY updated DESC, id DESC LIMIT ?",
                         (uid, ai_plan.KIND, since, LIST_MAX)).fetchall()
    groups = {g: [] for g in GROUP_ORDER}
    for r in rows:
        g = jobs.group_of(r[2])
        if g is None:
            continue
        groups[g].append({"id": r[0], "title": r[1], "state": r[2], "sub": r[3], "group": g, "state_text": state_text(r[2]),
                          "line": _line(r[2], r[3], r[4], None), "created": r[5], "updated": r[6], "plan_v": r[7]})
    want = [group] if group in groups else list(GROUP_ORDER)
    return {"groups": [{"key": g, "label": group_text(g), "items": groups[g]} for g in want],
            "counts": {g: len(groups[g]) for g in GROUP_ORDER}}


_EV_SKIP = ("units",)


def _event_text(kind, d, labels):
    d = d if isinstance(d, dict) else {}
    if kind == "state":
        if d.get("to") == "wartet_ok":
            return t("Plan vorgeschlagen")
        tx = state_text(d.get("to"))
        why = reason_text(d.get("to"), None, d.get("reason"))
        return tx + (f" – {why}" if why and why != tx else "")
    if kind == "read":
        names = [x for x in (ai_plan.read_text(r, labels) for r in d.get("read") or ()) if x]
        return (t("Gelesen:") + " " + ", ".join(dict.fromkeys(names))) if names else None
    if kind == "step":
        return t("Schritt {n} erledigt", n=int(d.get("n") or 0) + 1) if d.get("outcome") == "done" else \
            t("Schritt {n} übersprungen", n=int(d.get("n") or 0) + 1)
    if kind == "plan":
        return t("Plan geändert – bitte neu freigeben")
    if kind == "approve":
        return t("Freigegeben") + (f" · {d['dev']}" if d.get("dev") else "")
    if kind == "undo":
        if d.get("requested"):
            return t("Rückgängig angestoßen")
        if d.get("conflict"):
            return t("Nicht zurückgenommen – inzwischen geändert")
        if d.get("blocked"):
            return t("Rückgängig nicht möglich")
        return t("Zurückgenommen")
    if kind == "model":
        return t("{name} war nicht erreichbar – neuer Versuch", name=ai.persona_name())
    if kind == "wait":
        return t("zurückgestellt") if d.get("reason") else None
    return None


def detail(uid, job_id):
    with _db() as c:
        r = c.execute("SELECT id, title, state, sub, reason, created, updated, ended_at, plan FROM job WHERE id=? AND uid=? AND kind=?",
                      (job_id, uid, ai_plan.KIND)).fetchone()
        if not r:
            return None
        chs = c.execute("SELECT id, step_n, target, before, after, rev_after, undone_at FROM job_change WHERE job_id=? ORDER BY id",
                        (job_id,)).fetchall()
        evs = c.execute("SELECT at, kind, data FROM job_event WHERE job_id=? ORDER BY id LIMIT 300", (job_id,)).fetchall()
        undo_cls = {x[0]: x[1] for x in c.execute("SELECT n, undo_class FROM job_step WHERE job_id=?", (job_id,)).fetchall()}
        checks = {x[0]: ai_undo.check_one(c, uid, (x[2], x[3], x[5])) for x in chs if x[6] is None} if r[2] == "erledigt" else {}
    pj = r[8] if isinstance(r[8], dict) else {}
    labels = pj.get("labels") or {}
    changes = []
    for cid, n, target, b, after, _rv, undone in chs:
        if not isinstance(b, dict) or b.get("op") == "none":
            continue
        why = checks.get(cid)
        changes.append({"id": cid, "step": n, "target": target, "label": ai_undo.label(target, after, labels),
                        "undo_class": undo_cls.get(n), "class_text": ai_plan.klass_text(undo_cls.get(n), "own"),
                        "undone": undone is not None, "can_undo": r[2] == "erledigt" and undone is None and why is None,
                        "why": why if why in ("changed", "gone", "no_undo") else None,
                        "version_url": ai_undo.version_url(job_id, cid) if why == "changed" and b.get("op") in ("note", "props") else None})
    out = {"id": r[0], "title": r[1], "state": r[2], "state_text": state_text(r[2]), "group": jobs.group_of(r[2]),
           "group_text": group_text(jobs.group_of(r[2])), "sub": r[3], "reason": r[4], "reason_text": reason_text(r[2], r[3], r[4]),
           "created": r[5], "updated": r[6], "ended_at": r[7], "plan": ai_plan.plan_view(job_id, uid), "changes": changes}
    appr = next((d for at, k, d in reversed(evs) if k == "approve"), None)
    at = next((at for at, k, d in reversed(evs) if k == "state" and isinstance(d, dict) and d.get("frm") == "wartet_ok"
               and d.get("to") == "laeuft"), None)
    if at:
        out["approved"] = {"at": at, "device": (appr or {}).get("dev")}
    if r[2] in ("erledigt", "zurueckgenommen"):
        out["report"] = report(r, changes)
    out["events"] = [{"at": at, "kind": k, "text": tx} for at, k, d in evs if k not in _EV_SKIP
                     for tx in [_event_text(k, d, labels)] if tx]
    acts = []
    if r[2] == "wartet_ok":
        acts += ["approve", "edit", "cancel"]
    elif r[2] in ("laeuft", "wartet_dich"):
        acts.append("cancel")
    elif r[2] == "erledigt" and any(x["can_undo"] for x in changes):
        acts.append("undo")
    out["actions"] = acts
    return out


def _done_word(target):
    kind = target.partition(":")[0]
    return {"task": t("angelegt"), "event": t("eingetragen"), "note": t("geändert"), "row": t("geändert")}.get(kind, "")


def report(r, changes):
    """Rueckmeldung: Ergebnis zuerst (Zug report bzw. undo), je Aenderung eine Zeile (aus dem Protokoll, nicht vom Modell)."""
    if r[2] == "zurueckgenommen":
        text = ai_plan.say("undo", {"target": r[1]})
    else:
        text = ai_plan.say("report", {"title": r[1], "changes": [c["label"] for c in changes]})
    return {"text": text, "lines": [{"change": c["id"], "text": f"{c['label']} {_done_word(c['target'])}".strip(),
                                     "undone": c["undone"], "can_undo": c["can_undo"]} for c in changes]}


# --- Endpunkte ----------------------------------------------------------------------------------------------------
@router.get("/api/ai/jobs")
def api_jobs_list(request: Request):
    uid, err = _who(request, widget=True)
    if err is not None:
        return err
    if not _allowed(uid):
        return _bad(t("Der KI-Assistent ist für dich nicht eingeschaltet."), 403)
    g = request.query_params.get("group")
    if g is not None and g not in GROUP_ORDER:
        return _bad(t("ungültige Angabe"))
    return JSONResponse(list_jobs(uid, g), headers=NO_STORE)


def _fmt(typ, data):
    return f"event: {typ}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


@router.post("/api/ai/jobs")
async def api_jobs_create(request: Request):
    """{text, source?, context?: {note}, choice?} oder {recipe: <kennung>, fields: {...}} (Rezept direkt, ohne Modell -
    z. B. "Als Aufgabe anlegen"). Antwort text/event-stream: status {phase}, dann plan | ask | refuse | error."""
    uid, err = _who(request)
    if err is not None:
        return err
    b = await _body(request)
    if b is None:
        return _bad(t("ungültige Angabe"))
    text = b.get("text") if isinstance(b.get("text"), str) else ""
    if len(text) > ai_plan.REQ_MAX:
        return _bad(t("Die Bitte ist zu lang (höchstens {n} Zeichen).", n=ai_plan.REQ_MAX))
    recipe = b.get("recipe")
    if recipe is not None and not isinstance(recipe, str):           # nie ein Rezept-Objekt aus dem Client (RT-21)
        return _bad(t("ungültige Angabe"))
    fields = b.get("fields") if isinstance(b.get("fields"), dict) else None
    if recipe is None and not text.strip():
        return _bad(t("Bitte sag mir, was ich tun soll."))
    ctxb = b.get("context") if isinstance(b.get("context"), dict) else {}
    note = ctxb.get("note")
    choice = b.get("choice") if isinstance(b.get("choice"), str) and len(b["choice"]) <= 200 else None
    source = b.get("source") if b.get("source") in jobs.SOURCES else "saga"
    if not await asyncio.to_thread(ai_plan.jobs_on, uid):
        allowed = await asyncio.to_thread(_allowed, uid)
        return _bad(t("Der KI-Assistent ist für dich nicht eingeschaltet.") if not allowed
                    else t("Aufträge gehen mit diesem Modell nicht – frag mich gern etwas."), 403)
    c, gerr = await ai._gate(uid)                                     # eine Anfrage je Person (Fragen-Spur)
    if gerr is not None:
        return gerr
    loop = asyncio.get_running_loop()
    q = asyncio.Queue()
    halt = threading.Event()
    ctx = contextvars.copy_context()
    state = {"released": False}

    def release():
        if not state["released"]:
            state["released"] = True
            ai._release(uid)

    def put(item):
        try:
            loop.call_soon_threadsafe(q.put_nowait, item)
        except RuntimeError:
            halt.set()

    def work():
        try:
            res = ai_plan.plan(uid, text, note=note, choice=choice, recipe=recipe, fields=fields, source=source,
                               emit_status=lambda d: put(("status", d)), stop=halt.is_set)
            put(("res", dict(res)))
        except Exception as ex:  # noqa: BLE001 - nie Inhalte loggen
            print(f"ai_jobs: {type(ex).__name__}: {ex}", flush=True)
            put(("res", {"type": "error", "status": 500, "error": t("Das hat nicht geklappt – nichts wurde geändert.")}))

    async def gen():
        th = threading.Thread(target=lambda: ctx.run(work), daemon=True, name="saga-ai-plan")
        try:
            yield ":" + " " * 2048 + "\n\n"
            yield _fmt("status", {"phase": "planning", "text": t("{name} denkt nach …", name=ai.persona_name())})
            th.start()
            while True:
                try:
                    kind, val = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                if kind == "status":
                    yield _fmt("status", dict(val, phase="planning"))
                    continue
                typ = val.pop("type", "error")
                yield _fmt(typ if typ in ("plan", "ask", "refuse", "error") else "error", val)
                return
        finally:
            halt.set()
            release()

    try:
        return StreamingResponse(gen(), media_type="text/event-stream; charset=utf-8", headers=ai.HEADERS)
    except Exception:
        release()
        raise


@router.get("/api/ai/jobs/{jid}")
def api_jobs_detail(jid: str, request: Request):
    uid, err = _who(request)
    if err is not None:
        return err
    i = _jid(jid)
    d = detail(uid, i) if i else None
    if d is None:
        return _bad("not found", 404)
    return JSONResponse(d, headers=NO_STORE)


@router.put("/api/ai/jobs/{jid}/plan")
async def api_jobs_plan(jid: str, request: Request):
    uid, err = _who(request)
    if err is not None:
        return err
    i = _jid(jid)
    b = await _body(request) or {}
    pv = b.get("plan_v")
    if not i or isinstance(pv, bool) or not isinstance(pv, int):
        return _bad(t("ungültige Angabe"))
    if not await asyncio.to_thread(_allowed, uid):
        return _bad(t("Der KI-Assistent ist für dich nicht eingeschaltet."), 403)
    st, out = await asyncio.to_thread(ai_plan.edit, i, uid, pv, b.get("edits"))
    return JSONResponse(out, status_code=st, headers=NO_STORE)


def _device(request):
    did = request.session.get("did")
    if did is None:
        return t("Web")
    try:
        with _db() as c:
            r = c.execute("SELECT name FROM device_login WHERE id=?", (int(did),)).fetchone()
        return (r[0] or "")[:60] if r else t("App")
    except Exception:  # noqa: BLE001
        return t("App")


def _approve(uid, i, pv, ph, dev):
    with _db() as c:
        r = c.execute("SELECT state, plan_v, plan_hash FROM job WHERE id=? AND uid=? AND kind=?", (i, uid, ai_plan.KIND)).fetchone()
    if not r:
        return 404, {"error": "not found"}
    if r[0] != "wartet_ok":
        return 409, {"error": t("Dieser Auftrag wartet nicht mehr auf dein OK."), "code": "state", "state": r[0], "state_text": state_text(r[0])}
    if r[1] != pv or r[2] != ph:
        return 409, {"error": t("Der Plan hat sich inzwischen geändert – bitte noch einmal ansehen."), "code": "stale", "plan_v": r[1]}
    if not jobs.approve(i, uid, pv, ph):
        with _db() as c:
            s = c.execute("SELECT state, plan_v FROM job WHERE id=?", (i,)).fetchone()
        return 409, {"error": t("Dieser Auftrag wartet nicht mehr auf dein OK."), "code": "state", "state": s[0] if s else None}
    with _db() as c, c:
        jobs._event(c, i, "approve", jobs.now(), dev=dev, plan_v=pv)
    return 200, {"ok": True, "state": "laeuft", "state_text": state_text("laeuft"), "say": ai_plan.say("start")}


@router.post("/api/ai/jobs/{jid}/approve")
async def api_jobs_approve(jid: str, request: Request):
    uid, err = _who(request)
    if err is not None:
        return err
    i = _jid(jid)
    b = await _body(request) or {}
    pv, ph = b.get("plan_v"), b.get("plan_hash")
    if not i or isinstance(pv, bool) or not isinstance(pv, int) or not isinstance(ph, str) or len(ph) != 64:
        return _bad(t("ungültige Angabe"))
    if not await asyncio.to_thread(_allowed, uid):
        return _bad(t("Der KI-Assistent ist für dich nicht eingeschaltet."), 403)
    st, out = await asyncio.to_thread(_approve, uid, i, pv, ph, _device(request))
    return JSONResponse(out, status_code=st, headers=NO_STORE)


@router.post("/api/ai/jobs/{jid}/cancel")
async def api_jobs_cancel(jid: str, request: Request):
    uid, err = _who(request)
    if err is not None:
        return err
    i = _jid(jid)
    if not i:
        return _bad("not found", 404)

    def run():
        with _db() as c:
            r = c.execute("SELECT state FROM job WHERE id=? AND uid=? AND kind=?", (i, uid, ai_plan.KIND)).fetchone()
        if not r:
            return 404, {"error": "not found"}
        if not jobs.cancel(i, uid):
            return 409, {"error": t("Dieser Auftrag läuft nicht mehr."), "code": "state", "state": r[0]}
        return 200, {"ok": True, "state": "abgebrochen", "state_text": state_text("abgebrochen"),
                     "text": t("Abgebrochen – nichts ist passiert.") if r[0] == "wartet_ok" else t("Abgebrochen.")}
    st, out = await asyncio.to_thread(run)
    return JSONResponse(out, status_code=st, headers=NO_STORE)


@router.post("/api/ai/jobs/{jid}/undo")
async def api_jobs_undo(jid: str, request: Request):
    uid, err = _who(request)
    if err is not None:
        return err
    i = _jid(jid)
    if not i:
        return _bad("not found", 404)
    b = await _body(request) or {}
    ch = b.get("change")
    if ch is not None:
        if isinstance(ch, bool) or not isinstance(ch, int) or not 0 < ch < 2 ** 63:
            return _bad(t("ungültige Angabe"))
        st, out = await asyncio.to_thread(ai_undo.undo_one, i, uid, ch)
        if st == 200:
            out["state"] = (await asyncio.to_thread(jobs.state_of, i) or {}).get("state")
        return JSONResponse(out, status_code=st, headers=NO_STORE)

    def start():
        with _db() as c:
            r = c.execute("SELECT state, undo_at FROM job WHERE id=? AND uid=? AND kind=?", (i, uid, ai_plan.KIND)).fetchone()
        if not r:
            return 404, {"error": "not found"}
        if r[0] != "erledigt" or r[1] is not None:
            return 409, {"error": t("Das lässt sich gerade nicht zurücknehmen."), "code": "state", "state": r[0]}
        bad = ai_undo.conflicts(i, uid)
        if bad:
            return 409, {"error": t("Das wurde seitdem geändert."), "code": "changed", "changes": bad,
                         "version_url": next((x["version_url"] for x in bad if x["why"] == "changed"), None)}
        if not jobs.undo(i, uid):
            return 409, {"error": t("Das lässt sich gerade nicht zurücknehmen."), "code": "state", "state": r[0]}
        return 202, None
    st, out = await asyncio.to_thread(start)
    if st != 202:
        return JSONResponse(out, status_code=st, headers=NO_STORE)
    t0 = time.monotonic()
    s = None
    while time.monotonic() - t0 < UNDO_WAIT:
        s = await asyncio.to_thread(jobs.state_of, i)
        if s and s["state"] != "erledigt":
            break
        await asyncio.sleep(0.1)
    st_now = (s or {}).get("state")
    out = {"ok": True, "state": st_now, "state_text": state_text(st_now)}
    if st_now == "zurueckgenommen":
        with _db() as c:
            title = (c.execute("SELECT title FROM job WHERE id=?", (i,)).fetchone() or [""])[0]
        out["text"] = ai_plan.say("undo", {"target": title})
    return JSONResponse(out, status_code=200, headers=NO_STORE)


@router.get("/api/ai/jobs/{jid}/changes/{cid}/before")
def api_jobs_before(jid: str, cid: str, request: Request):
    uid, err = _who(request)
    if err is not None:
        return err
    i, k = _jid(jid), _jid(cid)
    if not i or not k:
        return _bad("not found", 404)
    st, out = ai_undo.before_view(i, uid, k)
    return JSONResponse(out, status_code=st, headers=NO_STORE)


@router.api_route("/api/ai/jobs/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
def api_jobs_other(rest: str, request: Request):
    """Alles andere unter /api/ai/jobs (act, outbox, inbox kommen mit K-C/K-E): mit fremdem Schluessel 403 (RT-42)."""
    _uid, err = _who(request)
    if err is not None and err.status_code == 403:
        return err
    return _bad("not found", 404)

