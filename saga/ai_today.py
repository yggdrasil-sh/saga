"""Tageszeile "Kurz gesagt" auf "Heute" (Saga 2.6, Bugs-DB 2271 K-D; Plan kd-plan §2, Doku docs/APP-API-KI.md §13).

Ohne Modell, ohne Netz: die Zeile nennt hoechstens drei Dinge des Tages beim Namen (naechster Termin, wichtigste
ueberfaellige bzw. heute faellige Aufgabe samt Aufgabenquellen, ab 18:00 der erste Termin von morgen, wenn heute nichts mehr
kommt) und darunter "Wartet auf dich" (Auftraege in wartet_ok/wartet_dich). Berechnet wird beim Abruf (kein Morgenlauf),
nur fuer Konten mit Faehigkeit "ai" - ohne Einwilligung, weil nichts an ein Sprachmodell geht.

    facts(c, uid, now, tz, events=None) -> [Fact]   Vorfilter ohne Modell (spaeter unveraendert von "Ideen" genutzt)
    render(facts, lang, persona, now, tz) -> (line|None, satzglieder, when)  Zeile aus Bausteinen (Zug "day")
    build(...) -> dict                               Antwort aus fertigen Fakten (rein, ohne Datenbank - Testvektoren)
    compute(uid, tz=None, lang=None) -> dict         Antwort von GET /api/ai/today (§13)
    widget(uid, tz=None, lang=None) -> dict          Teil "line" von GET /api/app/today?line=1
    changed(uid)                                     Live ai.today.changed {v} (entprellt, nach dem Commit)

Termine nur aus dem Speicher (calendars.cached): fehlt ein Kalender, kommt die Antwort sofort ohne Termine mit
pending ["calendar"], der Kalender laedt im Hintergrund nach, danach ai.today.changed. Zustand der Person: user_kv
"ai_today" = {off?, x: [<= 3 Zeitpunkte von X], asked?}; Ruhe ueber ai_saga.set_quiet/quiet_until(uid, "today") bis
05:00 Ortszeit (ai_saga.next_local). Tests: tests/kollegin_ideen.py, Vektoren tests/today_vectors.json
(tools/today_vectors.py gen|check), Messung tools/perf/kollegin_heute.py."""
import asyncio
import hashlib
import json
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from db import connect as _db
from saga import bus, caps, testclock
from saga.core import TZ, _api_guard, _bad, _body, t

router = APIRouter()

KV = "ai_today"
QUIET_WHERE = "today"
QUIET_HOUR = 5                       # Ruhe "bis morgen" endet um 05:00 Ortszeit
ASK_AFTER = 3                        # nach 3x X ...
ASK_DAYS = 14                        # ... in 14 Tagen einmal fragen
PARTS_MAX = 3
LINE_MAX = 160                       # Grapheme (P1)
TITLE_MAX = 40                       # Grapheme, sonst mit "…"
SENTENCE_MAX = 2                     # Dinge im Satz (sentence)
EVENING_HOUR = 18                    # ab hier: erster Termin von morgen, wenn heute nichts mehr kommt
KINDS = ("event", "over", "due", "tomorrow")           # Reihenfolge = Prioritaet
TARGETS = ("task", "row", "tasks", "events", "jobs")
WAITING_STATES = ("wartet_ok", "wartet_dich")
WIDGET_KEYS = ("enabled", "on", "line", "waiting", "counts", "quiet", "v")
DEBOUNCE = 0.3                       # s: ai.today.changed je Person zusammenfassen


@dataclass
class Fact:
    kind: str                        # event | over | due | tomorrow
    key: str                         # eindeutig (t:<id>, r:<id>, e:<n>)
    prio: int                        # 1 Termin, 2 ueberfaellig, 3 heute, 4 morgen
    fields: dict = field(default_factory=dict)        # title, time?, allday?
    target: dict = field(default_factory=dict)


# --- Zustand der Person -------------------------------------------------------------------------------------------
def _state(c, uid):
    r = c.execute("SELECT v FROM user_kv WHERE uid=? AND k=?", (uid, KV)).fetchone()
    try:
        d = json.loads(r[0]) if r and r[0] else {}
    except (TypeError, ValueError):
        d = {}
    return d if isinstance(d, dict) else {}


def _save(c, uid, d):
    d = {k: v for k, v in d.items() if k in ("off", "x", "asked") and v not in (None, False, [], "")}
    if d:
        c.execute("INSERT OR REPLACE INTO user_kv (uid, k, v) VALUES (?, ?, ?)", (uid, KV, json.dumps(d, sort_keys=True)))
    else:
        c.execute("DELETE FROM user_kv WHERE uid=? AND k=?", (uid, KV))


def _recent_x(d, now):
    try:
        return [float(x) for x in d.get("x") or () if float(x) > now - ASK_DAYS * 86400][-ASK_AFTER:]
    except (TypeError, ValueError):
        return []


# --- Zeit ---------------------------------------------------------------------------------------------------------
def _zone(c, uid, tz):
    from saga import tasksrc
    if isinstance(tz, ZoneInfo):
        return tz
    if isinstance(tz, str) and tz in tasksrc._zones():
        return ZoneInfo(tz)
    return tasksrc._acct_tz(c, uid, tasksrc.digest_cfg(c, uid))


def _zname(z):
    return getattr(z, "key", None) or str(z)


def _hm(dt):
    return dt.strftime("%H:%M")


def fmt_time(hm, lang):
    """"HH:MM" -> Uhrzeit wie im Kalender (static/calendar.js: Intl.DateTimeFormat(SAGA_LOCALE, {hour: '2-digit', minute:
    '2-digit'})): en-US "07:30 PM" (schmales Leerzeichen wie ICU), sonst 24 h "19:30"."""
    if not hm:
        return None
    if lang != "en":
        return hm
    try:
        h, m = (int(x) for x in str(hm).split(":")[:2])
    except ValueError:
        return hm
    return f"{(h % 12) or 12:02d}:{m:02d}\u202f{'AM' if h < 12 else 'PM'}"


def _daypart(h):
    return "morning" if 5 <= h < 11 else ("day" if 11 <= h < 18 else "evening")


# --- Fakten -------------------------------------------------------------------------------------------------------
def _dt(s, tz):
    try:
        d = datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=TZ)
    return d.astimezone(tz)


def _day(s):
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def _event_facts(events, loc):
    """Termine (calendars.cached, Serverzeit) -> Fakten auf dem Tag der Person: event (heute, nicht vorbei; ganztaegig mit
    allday) und tomorrow (Termine von morgen - render nimmt sie nur ab 18:00, wenn heute nichts mehr kommt)."""
    tz, D = loc.tzinfo, loc.date()
    out = []
    for i, e in enumerate(events or ()):
        title = " ".join(str(e.get("summary") or "").split()) or t("(ohne Titel)")
        if e.get("allday"):
            a, b = _day(e.get("start")), _day(e.get("end"))
            if a is None:
                continue
            b = b if b and b > a else a + timedelta(days=1)       # Ende exklusiv
            for kind, day in (("event", D), ("tomorrow", D + timedelta(days=1))):
                if a <= day < b:
                    out.append(Fact(kind, f"e:{i}:{kind}", 1 if kind == "event" else 4,
                                    {"title": title, "allday": True, "sort": ""}, {"type": "events", "date": day.isoformat()}))
            continue
        s, en = _dt(e.get("start"), tz), _dt(e.get("end"), tz)
        if s is None:
            continue
        end = en or s
        if s.date() == D and end.timestamp() > loc.timestamp():
            out.append(Fact("event", f"e:{i}", 1, {"title": title, "time": _hm(s), "sort": s.isoformat()},
                            {"type": "events", "date": D.isoformat()}))
        elif s.date() == D + timedelta(days=1):
            out.append(Fact("tomorrow", f"e:{i}", 4, {"title": title, "time": _hm(s), "sort": s.isoformat()},
                            {"type": "events", "date": s.date().isoformat()}))
    out.sort(key=lambda f: (f.kind, bool(f.fields.get("allday")), f.fields.get("sort") or "", f.key))
    return out


def _task_facts(c, uid, D, tz):
    """Offene Aufgaben (Eingang + Aufgabenquellen, Rechte wie die Aufgabenliste) -> over / due."""
    from saga import tasksrc
    items, _src, _ppl = tasksrc.collect(c, uid, D, tz, clean=False, until=(D + timedelta(days=1)).isoformat())   # schlanker Weg
    d0 = D.isoformat()
    out = []
    for it in items:
        due = it.get("due")
        if not due:
            continue
        if it.get("overdue"):
            kind = "over"
        elif due <= d0 <= (it.get("due_end") or due):
            kind = "due"
        else:
            continue
        tgt = {"type": "task", "id": it["id"]} if it.get("kind") == "task" else {"type": "row", "id": it["id"]}
        out.append(Fact(kind, it["key"], 2 if kind == "over" else 3,
                        {"title": it.get("text") or t("Unbenannt"), "prio": it.get("prio") or 2,
                         "due": due, "time": it.get("time") or ""}, tgt))
    out.sort(key=lambda f: (f.kind, f.fields["prio"], f.fields["due"], f.fields["time"], f.fields["title"].lower(), f.key))
    return out                           # Titel ungekuerzt; render/_short glaettet Leerraum


def facts(c, uid, now, tz, events=None):
    """Vorfilter ohne Modell: Fakten des Tages der Person (Zone tz) - Termine nur, wenn events (aus dem Speicher) da sind."""
    loc = datetime.fromtimestamp(now, tz)
    return (_event_facts(events, loc) if events is not None else []) + _task_facts(c, uid, loc.date(), tz)


# --- Text ---------------------------------------------------------------------------------------------------------
def _short(s, n=TITLE_MAX):
    from saga.ai_say import graphemes
    s = " ".join(str(s or "").split())[:400]
    if graphemes(s) <= n:
        return s
    out = ""
    for ch in s:
        if graphemes(out + ch) > n - 1:
            break
        out += ch
    return out.rstrip() + "…"


def _pick(fs, loc):
    """Fakten -> [(kind, erster Fakt, weitere)] in Prioritaet; Termine mit Uhrzeit vor ganztaegigen, "morgen" nur ab 18:00
    ohne weiteren Termin mit Uhrzeit heute (dann statt eines ganztaegigen von heute)."""
    by = {k: [f for f in fs if f.kind == k] for k in KINDS}
    timed = [f for f in by["event"] if not f.fields.get("allday")]
    evening = loc.hour >= EVENING_HOUR and not timed and by["tomorrow"]
    out = []
    if timed:
        out.append(("event", timed[0], len(timed) - 1))
    elif by["event"] and not evening:
        out.append(("event", by["event"][0], 0))
    for k in ("over", "due"):
        if by[k]:
            out.append((k, by[k][0], len(by[k]) - 1))
    if evening:
        tm = [f for f in by["tomorrow"] if not f.fields.get("allday")] or by["tomorrow"]
        out.append(("tomorrow", tm[0], 0))
    return out


def _say(lang, persona, **f):
    from saga import ai_say
    return ai_say.say("day", f, lang, persona)


def render(fs, lang, persona, now, tz):
    """-> (line | None, satzglieder fuer sentence, when). Hoechstens 3 Teile und 160 Grapheme - Ueberlauf nach Prioritaet weg."""
    from saga.ai_say import graphemes
    loc = datetime.fromtimestamp(now, tz)
    parts = []
    for kind, f, more in _pick(fs, loc)[:PARTS_MAX]:
        part = kind + (".allday" if f.fields.get("allday") else "")
        title, tm = _short(f.fields.get("title")), fmt_time(f.fields.get("time"), lang)
        base = _say(lang, persona, part=part, title=title, time=tm)
        if not base:
            continue
        p = {"kind": kind, "text": _say(lang, persona, part=part, title=title, time=tm, more=more) if more else base,
             "target": dict(f.target)}
        a11y = base
        if more:
            p["n"] = more + 1
            a11y = base + ", " + _say(lang, persona, part="a11y.more", more=more)
        parts.append((p, a11y, _say(lang, persona, part="s." + part, title=title, time=tm)))
    while len(parts) > 1 and graphemes(" · ".join(p["text"] for p, _, _ in parts)) > LINE_MAX:
        parts.pop()
    if not parts:
        return None, [], "today"
    today = [g for p, _, g in parts if p["kind"] != "tomorrow" and g]
    when = "today" if today else "tomorrow"
    items = (today or [g for _, _, g in parts if g])[:SENTENCE_MAX]
    line = {"text": " · ".join(p["text"] for p, _, _ in parts), "a11y": t("Kurz gesagt") + ": " + ", ".join(a for _, a, _ in parts),
            "parts": [p for p, _, _ in parts]}
    return line, items, when


def _waiting(n, lang, persona):
    if not n:
        return None
    return {"n": n, "text": _say(lang, persona, part="waiting", n=n), "target": {"type": "jobs", "group": "wartet_auf_dich"}}


def _info(lang, persona):
    from saga import ai_say
    return (ai_say.formal("day.info", lang, persona)
            or t("Ohne KI berechnet – aus deinen Aufgaben, Terminen und Aufträgen. Nichts davon geht an ein Sprachmodell."))


def _v(d):
    return hashlib.sha256(json.dumps({k: v for k, v in d.items() if k != "v"}, sort_keys=True,
                                     ensure_ascii=False).encode()).hexdigest()[:12]


def build(fs, *, now, tz, lang, persona, name, st=None, quiet_until=None, waiting_n=0, pending=(), events_n=0):
    """Antwort aus fertigen Fakten - rein (ohne Datenbank), auch fuer tools/today_vectors.py. events_n None = Kalender
    noch unbekannt. Texte in lang (Systemtexte ueber t(), Saetze ueber Zug "day")."""
    from saga import ai_say, i18n
    lang = ai_say.lang_of(lang)
    tok = i18n.set_lang(lang)
    try:
        st = st or {}
        loc = datetime.fromtimestamp(now, tz)
        on = not st.get("off")
        line, items, when = render(fs, lang, persona, now, tz)
        quiet = ({"until": datetime.fromtimestamp(quiet_until, tz).isoformat(timespec="seconds"), "text": t("Ruhe bis morgen"),
                  "undo": t("Wieder an")} if quiet_until and on else None)
        ask = None
        if quiet and not st.get("asked") and len(_recent_x(st, now)) >= ASK_AFTER:
            ask = {"text": t("Tageszeile ausblenden?"), "yes": t("Ja, ausblenden"), "no": t("Nein, passt")}
        day = _say(lang, persona, part="list", items=items, when=when)
        sentence = ai_say.say("greet", {"person": name, "daypart": _daypart(loc.hour), "day": day}, lang, persona) or None
        waiting = _waiting(waiting_n, lang, persona)
        counts = {"events": events_n, "over": sum(1 for f in fs if f.kind == "over"),
                  "due": sum(1 for f in fs if f.kind == "due"), "waiting": waiting_n or 0}
        out = {"enabled": True, "on": on, "day": loc.date().isoformat(), "tz": _zname(tz), "label": t("Kurz gesagt"),
               "line": line if on and not quiet else None, "sentence": sentence, "waiting": waiting, "counts": counts,
               "quiet": quiet, "ask": ask, "pending": list(pending), "info": _info(lang, persona)}
        out["v"] = _v(out)
        return out
    finally:
        i18n._cur.reset(tok)


# --- Antwort ------------------------------------------------------------------------------------------------------
_PENDING = {}                        # uid -> Zeitpunkt: Antwort ohne Kalender geliefert, Nachladen laeuft


def _name(uid):
    import auth
    return (str((auth.user_info(uid) or {}).get("name") or uid).split() or [uid])[0]


def _n_waiting(c, uid):
    return c.execute("SELECT count(*) FROM job WHERE uid=? AND state IN (?, ?)", (uid, *WAITING_STATES)).fetchone()[0] or 0


def _events(uid, now, tz):
    """-> (events | None, n_heute | None). Schluessel wie Heute-Leiste und Widget: (heute, heute+2) in Serverzeit."""
    from saga import calendars
    d0 = datetime.fromtimestamp(now, TZ).date()
    evs, cold = calendars.cached(uid, d0, d0 + timedelta(days=2))
    if cold:
        _PENDING[uid] = now
        return None, None
    _PENDING.pop(uid, None)
    return evs, sum(1 for f in _event_facts(evs, datetime.fromtimestamp(now, tz)) if f.kind == "event")


def compute(uid, tz=None, lang=None, now=None, conn=None):
    """Antwort von GET /api/ai/today (§13). Ohne Faehigkeit "ai": {"enabled": false}."""
    if conn is None:
        with _db() as c:
            return compute(uid, tz, lang, now, c)
    c = conn
    if not caps.can(uid, "ai", c):
        return {"enabled": False}
    from saga import ai_saga, ai_say, i18n
    now = testclock.now() if now is None else now
    z = _zone(c, uid, tz)
    evs, n_ev = _events(uid, now, z)
    fs = facts(c, uid, now, z, evs)
    return build(fs, now=now, tz=z, lang=lang or i18n.get_lang(), persona=ai_say.persona_for(uid, c), name=_name(uid),
                 st=_state(c, uid), quiet_until=ai_saga.quiet_until(uid, QUIET_WHERE, c), waiting_n=_n_waiting(c, uid),
                 pending=() if evs is not None else ("calendar",), events_n=n_ev)


def widget(uid, tz=None, lang=None):
    """Teil "line" fuer GET /api/app/today?line=1 (ohne sentence/ask/info)."""
    d = compute(uid, tz, lang)
    return {k: d[k] for k in WIDGET_KEYS if k in d}


def export(uid, conn=None):
    """Fuer "Meine Daten" (ai_saga.export): Schalter, Zeitpunkte von X, Frage beantwortet, Ruhe."""
    if conn is None:
        with _db() as c:
            return export(uid, c)
    from saga import ai_saga
    st = _state(conn, uid)
    return {"on": not st.get("off"), "dismissed": st.get("x") or [], "asked": bool(st.get("asked")),
            "quiet_until": ai_saga.quiet_until(uid, QUIET_WHERE, conn)}


# --- Live ---------------------------------------------------------------------------------------------------------
_CH, _CH_LOCK = {}, threading.Lock()


def changed(uid):
    """ai.today.changed {v} an die Person - je Person zusammengefasst (DEBOUNCE), in Konto-Sprache und -Zone gerechnet;
    Clients laden neu, wenn v vom eigenen abweicht."""
    if not uid:
        return
    with _CH_LOCK:
        if uid in _CH:
            return
        tm = threading.Timer(DEBOUNCE, _send, (uid,))
        tm.daemon = True
        _CH[uid] = tm
    tm.start()


def _send(uid):
    with _CH_LOCK:
        _CH.pop(uid, None)
    try:
        from saga.core import account_lang
        d = compute(uid, None, account_lang(uid))
        if d.get("enabled"):
            bus.emit("direct", {"uids": [uid], "typ": "ai.today.changed", "data": {"v": d["v"]}})
    except Exception as ex:  # noqa: BLE001 - ein Live-Hinweis darf nie stoeren
        print(f"ai_today: changed {type(ex).__name__}: {ex}", flush=True)


def _after(c, uid, widget=False):
    if not uid:
        return
    if widget:
        from saga import widget_push
        widget_push.mark(c, uid, today=True)
    after = getattr(c, "after_commit", None)
    if after is None:
        changed(uid)
    else:
        after(lambda: changed(uid))


def _on_task(c, uid=None, **_):
    _after(c, uid)


def _on_note_saved(c, note_id=None, row=None, **_):
    if row is None or note_id is None:
        return
    for (u,) in c.execute("SELECT uid FROM task_source WHERE db_id=?", (note_id,)).fetchall():
        _after(c, u)


def _on_job_state(c, uid=None, old=None, new=None, **_):
    if (old in WAITING_STATES) != (new in WAITING_STATES):              # in/aus "Wartet auf dich"
        _after(c, uid, widget=True)


def _on_cal_loaded(uid):
    if _PENDING.pop(uid, None) is not None:
        changed(uid)


bus.on("task.created", _on_task)
bus.on("task.completed", _on_task)
bus.on("task.changed", _on_task)
bus.on("note.saved", _on_note_saved)
bus.on("job.state", _on_job_state)


def _hook_calendar():
    from saga import calendars
    calendars.on_loaded(_on_cal_loaded)


_hook_calendar()


# --- Schreiben ----------------------------------------------------------------------------------------------------
def set_prefs(uid, b):
    """PUT {on?, asked?} -> None oder Fehlertext. Falsche Werte vor jedem Schreiben."""
    if not isinstance(b, dict) or not b or set(b) - {"on", "asked"} or any(not isinstance(v, bool) for v in b.values()):
        return t("ungültige Angabe")
    from saga import ai_saga
    with _db() as c, c:
        st = _state(c, uid)
        if "on" in b:
            st["off"] = not b["on"]
            if b["on"]:
                ai_saga.set_quiet(uid, QUIET_WHERE, None, c)
        if b.get("asked"):
            st["asked"] = True
        _save(c, uid, st)
        _after(c, uid, widget=True)
    return None


def quiet(uid, on, tz=None, now=None):
    """X an der Zeile (on=True): Ruhe bis morgen 05:00 Ortszeit, Zeitpunkt merken; "Wieder an" (on=False)."""
    from saga import ai_saga
    now = testclock.now() if now is None else now
    with _db() as c, c:
        if on:
            st = _state(c, uid)
            ai_saga.set_quiet(uid, QUIET_WHERE, ai_saga.next_local(QUIET_HOUR, _zname(_zone(c, uid, tz)), now), c)
            st["x"] = (_recent_x(st, now) + [round(now, 3)])[-ASK_AFTER:]
            _save(c, uid, st)
        else:
            ai_saga.set_quiet(uid, QUIET_WHERE, None, c)
        _after(c, uid, widget=True)


# --- Schluessel und Endpunkte -------------------------------------------------------------------------------------
def _who(request):
    """-> (uid, fehlerantwort). Nur Sitzung bzw. Geraete-Anmeldung mit X-Saga; jeder Bearer (sga_, OAuth, hk_) 403."""
    if (request.headers.get("authorization") or "").strip():
        return None, _bad(t("Die Tageszeile gibt es nur in Saga selbst – nicht über API-Schlüssel."), 403)
    uid = _api_guard(request)
    return (uid, None) if uid else (None, _bad("unauthorized", 401))


def _answer(request, d):
    v = d.get("v")
    hdr = {"Cache-Control": "private, no-cache"}
    if v:
        hdr["ETag"] = f'"{v}"'
        inm = (request.headers.get("if-none-match") or "").strip()
        if inm and v in {x.strip().removeprefix("W/").strip('"') for x in inm.split(",")}:
            return Response(status_code=304, headers=hdr)
    return JSONResponse(d, headers=hdr)


def _tzq(request):
    return request.query_params.get("tz") or None


def _noai():
    return _bad(t("Der KI-Assistent ist für dich nicht eingeschaltet."), 403)


@router.get("/api/ai/today")
async def api_today(request: Request):
    uid, err = _who(request)
    if err:
        return err
    d = await asyncio.to_thread(compute, uid, _tzq(request))
    return _answer(request, d)


@router.put("/api/ai/today")
async def api_today_put(request: Request):
    uid, err = _who(request)
    if err:
        return err
    b = await _body(request)

    def run():
        if not caps.can(uid, "ai"):
            return _noai()
        e = set_prefs(uid, b)
        if e:
            return _bad(e)
        return JSONResponse(compute(uid, _tzq(request)), headers={"Cache-Control": "no-store"})
    return await asyncio.to_thread(run)


async def _quiet(request, on):
    uid, err = _who(request)
    if err:
        return err

    def run():
        if not caps.can(uid, "ai"):
            return _noai()
        quiet(uid, on, _tzq(request))
        return JSONResponse(compute(uid, _tzq(request)), headers={"Cache-Control": "no-store"})
    return await asyncio.to_thread(run)


@router.post("/api/ai/today/quiet")
async def api_today_quiet(request: Request):
    return await _quiet(request, True)


@router.delete("/api/ai/today/quiet")
async def api_today_quiet_off(request: Request):
    return await _quiet(request, False)
