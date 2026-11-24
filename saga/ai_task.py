"""Runner ai.task (Saga 2.6, Bugs-DB 2266 K-A6, Konzept K2/K5/K21): Geruest fuer Auftraege der Kollegin mit fester
Schrittliste - der Planer (Plan aus einer Bitte, Modellaufrufe zwischen den Schritten) kommt mit K-B. Je Takt fuehrt
saga.jobs EINEN Schritt aus; hier ist ein Schritt genau EIN Werkzeugaufruf ueber saga.tools (dieselben Wege wie MCP).

Ablauf eines Schritts (saga.jobs hat vorher Konto, Faehigkeiten und Einwilligung geprueft und den Schritt beansprucht):
 1. Plan unveraendert? plan_hash ueber Plan + Schritte wie bei der Freigabe - sonst fehlgeschlagen 'plan_geaendert'
    (nichts geschrieben: Angaben nach der Freigabe verbogen = Ziele ausserhalb des freigegebenen Plans).
 2. Werkzeug aus der Registry, fuer Auftraege freigegeben, Klasse wie im Plan - sonst fehlgeschlagen 'werkzeug'/'klasse'.
 3. JobTok aus dem Plan (tools.plan_tok): Bereiche und Ziele nur des Plans (Aufgaben/Kalender nur mit genau diesen
    Kennungen), reads = plan["reads"] ("Ich lese dafuer"), issued = letzte Freigabe (jobs.approved_at). Kein Tok ->
    wartet_dich 'abgemeldet' (Passwortwechsel/"ueberall abmelden" nach der Freigabe; Fortsetzen = neue Freigabe).
 4. Hintergrund-Spur ai_units.lane_take("bg", "job:<id>") fuer den ganzen Schritt; belegt -> zurueckgestellt 'spur'
    (LANE_WAIT s). Fragen bleiben vorn (saga/ai_units.py, Spuren); AI_MAX_PARALLEL=1 unveraendert.
 5. Einheiten im Pool job (ai_units.reserve, ab dem zweiten Schritt job_running): Schaetzung = Angaben/4 + STEP_EST.
    Pool voll -> ai_units.NoBudget -> saga.jobs stellt zurueck bis zum naechsten Tag ('budget', nicht fehlgeschlagen).
    Belegung und ihr Protokoll-Eintrag job_event 'units' {n, est, day, open: 1} in EINER Transaktion; die Abrechnung
    (Ergebnis = gelesen, Angaben = geschrieben, je Zeichen/4) ebenso und nur, solange open = 1. Stirbt der Server
    mitten im Schritt, gibt resume() (saga.jobs ruft es bei der Wiederaufnahme) offene Belegungen genau einmal frei.
 6. tools.run_step(job, step, tok, attempt=ctx.attempt): jede Wirkung mit diesem Versuch gestempelt (job_change) -
    ein zweiter Worker nach Lease-Ablauf schreibt nicht (StepTaken).
 7. Ergebnis: done mit job_step.result {tool, chars} (kein Inhalt). Lesende Schritte belegen ihre Ziele im Protokoll
    (ai_ctx.record -> job_event 'read'). Fehler des Werkzeugs: 404/403 -> wartet_dich 'kein_zugriff' (Seite nicht mehr
    geteilt, ausserhalb der Ziele), 409 -> wartet_dich 'konflikt' (rev veraltet), 401 -> wartet_dich 'abgemeldet',
    429 -> zurueckgestellt 'rate' (RATE_WAIT s), 400 -> fehlgeschlagen 'ungueltig', sonst fehlgeschlagen 'werkzeug'.
Ruecknahme (compensate) kommt mit K-B (Vorher-Stand in job_change).
Tests: tests/kollegin_jobs.py (run_step, Freigabe/Abmelden), tests/kollegin_fehler.py (kill -9, Tageslimit, Seite nicht
mehr geteilt), tests/kollegin_werkzeuge.py (Stempel je Werkzeug), tests/kollegin_budget.py; Messung tools/perf/bench_kollegin.py."""
import json

from db import connect as _db
from saga import ai_ctx, ai_units, jobs, tools
from saga.api_v1 import Fail

KIND = "ai.task"
STEP_EST = 500           # Einheiten: Vorab-Schaetzung fuer das Ergebnis eines Werkzeugs (~2.000 Zeichen) zusaetzlich zu den Angaben
LANE_WAIT = 30           # s: Hintergrund-Spur belegt -> so viel spaeter erneut
RATE_WAIT = 60           # s: Ratenbegrenzung des Auftrags (429) -> so viel spaeter erneut
_ASK = {404: "kein_zugriff", 403: "kein_zugriff", 409: "konflikt", 401: "abgemeldet"}


def _norm(rows):
    """job_step-Zeilen -> Schritte in der Form von jobs._norm_steps (fuer plan_hash)."""
    return [{"tool": r[1], "klass": r[2], "undo_class": r[3], "args": r[4], "cond": r[5], "client": bool(r[6])} for r in rows]


def _text(out):
    if out is None:
        return ""
    return out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)


def _reserve(job, n, est, running):
    """Belegen + Protokoll in einer Transaktion -> ai_units.Res (ref = job_event) oder None (Pool voll)."""
    with _db() as c, c:
        res = ai_units.reserve(job["uid"], "job", est, job_running=running, conn=c)
        if res is None:
            return None
        res.ref = c.execute("INSERT INTO job_event (job_id, at, kind, data) VALUES (?, ?, 'units', CAST(? AS JSONB)) RETURNING id",
                            (job["id"], jobs.now(), jobs._j({"n": n, "est": res.est, "day": res.day, "open": 1}))).fetchone()[0]
    return res


def _settle(job_id, res, in_chars, out_chars):
    """Abrechnen, nur solange die Belegung offen ist (sonst hat resume sie schon freigegeben) -> Einheiten."""
    units = ai_units.cost(in_chars, out_chars)
    with _db() as c, c:
        if not c.execute("UPDATE job_event SET data = data || CAST(? AS JSONB) WHERE id=? AND job_id=? AND kind='units' "
                         "AND data->>'open'='1'", (jobs._j({"open": 0, "units": units}), res.ref, job_id)).rowcount:
            res.settled = True
            return 0
        return ai_units.settle(res, in_chars, out_chars, conn=c)


def resume(job, n):
    """saga.jobs: ein frueherer Versuch kam nicht zum Abschluss (Server-Ende, Lease abgelaufen) - offene Belegungen
    dieses Auftrags freigeben (genau einmal: Zeilensperre, open = 1 -> 0)."""
    with _db() as c, c:
        for eid, d in c.execute("SELECT id, data FROM job_event WHERE job_id=? AND kind='units' AND data->>'open'='1' ORDER BY id "
                                "FOR UPDATE", (job["id"],)).fetchall():
            c.execute("UPDATE job_event SET data = data || CAST(? AS JSONB) WHERE id=?", (jobs._j({"open": 0, "units": 0, "freed": 1}), eid))
            ai_units.settle(ai_units.Res(job["uid"], "job", str(d["day"]), int(d["est"])), 0, 0, conn=c)


def run(ctx, job, step):
    spec = tools.get(step.get("tool"))
    if spec is None or not spec.kollegin:
        return jobs.Outcome.fail("werkzeug")
    if spec.klass != step.get("klass"):
        return jobs.Outcome.fail("klasse")
    with _db() as c:
        r = c.execute("SELECT plan_hash FROM job WHERE id=?", (ctx.job_id,)).fetchone()
        rows = c.execute("SELECT n, tool, klass, undo_class, args, cond, client FROM job_step WHERE job_id=? ORDER BY n",
                         (ctx.job_id,)).fetchall()
        issued = jobs.approved_at(c, ctx.job_id)
    plan = job.get("plan") if isinstance(job.get("plan"), dict) else {}
    if not r or jobs.plan_hash(plan, _norm(rows)) != r[0]:
        return jobs.Outcome.fail("plan_geaendert")
    tok = tools.plan_tok(ctx.uid, ctx.job_id, [{"tool": x[1], "args": x[4]} for x in rows], plan.get("reads") or (), issued=issued)
    if tok is None:
        return jobs.Outcome.ask("abgemeldet")
    args = step.get("args") or {}
    wrote = len(jobs._j(args))
    key = f"job:{ctx.job_id}"
    if not ai_units.lane_take("bg", key):
        return jobs.Outcome.defer(ctx.now + LANE_WAIT, "spur")
    out, fail, units = None, None, 0
    try:
        res = _reserve(job, ctx.step, ai_units.units_of(wrote) + STEP_EST, ctx.step > 0)
        if res is None:
            raise ai_units.NoBudget("job")      # saga.jobs: zurueckstellen bis morgen
        try:
            out = tools.run_step(job, {"n": ctx.step, "tool": spec.name, "args": args, "idem_key": ctx.idem}, tok, attempt=ctx.attempt)
        except Fail as f:
            fail = f
        finally:                                 # auch bei StepTaken/Absturz des Werkzeugs: Belegung abrechnen (ohne Ergebnis 0)
            units = _settle(ctx.job_id, res, len(_text(out)), wrote if out is not None else 0)
    finally:
        ai_units.lane_free("bg", key)
    if fail is not None:
        if fail.status in _ASK:
            return jobs.Outcome.ask(_ASK[fail.status], units=units)
        if fail.status == 429:
            return jobs.Outcome.defer(ctx.now + RATE_WAIT, "rate", units=units)
        return jobs.Outcome.fail("ungueltig" if fail.status == 400 else "werkzeug", units=units)
    if not spec.write:
        with _db() as c, c:
            ai_ctx.record(c, job_id=ctx.job_id, ctx=ai_ctx.Ctx(read=spec.refs(args)))
    return jobs.Outcome.done({"tool": spec.name, "chars": len(_text(out))}, units=units)


def register():
    jobs.register(KIND, run, resume=resume)


register()
