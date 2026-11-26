"""Tageszeile "Kurz gesagt" auf "Heute" (Saga 2.6, Bugs-DB 2271 K-D; Plan kd-plan §2, Doku docs/APP-API-KI.md §13).

Ohne Modell, ohne Netz: die Zeile nennt hoechstens drei Dinge des Tages beim Namen (naechster Termin, wichtigste
ueberfaellige bzw. heute faellige Aufgabe, ab 18:00 der erste Termin von morgen) und darunter "Wartet auf dich" (Auftraege
in wartet_ok/wartet_dich). Berechnet wird beim Abruf (kein Morgenlauf), nur fuer Konten mit Faehigkeit "ai" - ohne
Einwilligung, weil nichts an ein Sprachmodell geht.

    facts(c, uid, now, tz) -> [Fact]          Vorfilter ohne Modell (spaeter unveraendert von "Ideen" genutzt)
    render(facts, lang, persona) -> dict|None Zeile aus Bausteinen
    compute(uid, tz=None, lang=None) -> dict  Antwort von GET /api/ai/today (§13)
    widget(uid, tz=None, lang=None) -> dict   Teil "line" von GET /api/app/today?line=1

Zustand der Person: user_kv "ai_today" = {off?, x: [<= 3 Zeitpunkte von X], asked?}; Ruhe "bis morgen 05:00".

Stand S1 (Vertrag): Antwortform vollstaendig, Werte vereinfacht - Fakten nur aus der eigenen Aufgabenliste (Tabelle
task), noch keine Termine und keine Aufgabenquellen; Texte ueber t() statt Zug "day"; Ruhe vorlaeufig im eigenen
Eintrag (Schluessel "q"). S2 ersetzt das durch tasksrc.collect, calendars.cached, say("day", ...) und den gemeinsamen
Ruhe-Helfer ai_saga.set_quiet/quiet_until(uid, "today")."""
import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from db import connect as _db
from saga import caps, testclock
from saga.core import _api_guard, _bad, _body, t

router = APIRouter()

KV = "ai_today"
QUIET_HOUR = 5                       # Ruhe "bis morgen" endet um 05:00 Ortszeit
ASK_AFTER = 3                        # nach 3x X ...
ASK_DAYS = 14                        # ... in 14 Tagen einmal fragen
PARTS_MAX = 3
LINE_MAX = 160                       # Grapheme (P1)
TITLE_MAX = 40
EVENING_HOUR = 18                    # ab hier: erster Termin von morgen, wenn heute nichts mehr kommt
TARGETS = ("task", "row", "tasks", "events", "jobs")
WAITING_STATES = ("wartet_ok", "wartet_dich")


@dataclass
class Fact:
    kind: str                        # event | over | due | tomorrow
    key: str
    prio: int
    fields: dict = field(default_factory=dict)
    target: dict = field(default_factory=dict)


# --- Zustand der Person -------------------------------------------------------------------------------------------
def _state(c, uid):
    r = c.execute("SELECT v FROM user_kv WHERE uid=? AND k=?", (uid, KV)).fetchone()
    try:
        d = json.loads(r[0]) if r else {}
    except (TypeError, ValueError):
        d = {}
    return d if isinstance(d, dict) else {}


def _save(c, uid, d):
    d = {k: v for k, v in d.items() if v not in (None, False, [], "")}
    if d:
        c.execute("INSERT OR REPLACE INTO user_kv (uid, k, v) VALUES (?, ?, ?)", (uid, KV, json.dumps(d, sort_keys=True)))
    else:
        c.execute("DELETE FROM user_kv WHERE uid=? AND k=?", (uid, KV))


def _recent_x(d, now):
    try:
        return [float(x) for x in d.get("x") or () if float(x) > now - ASK_DAYS * 86400][-ASK_AFTER:]
    except (TypeError, ValueError):
        return []


def _next_five(now, tz):
    """Naechster 05:00 Ortszeit nach now (Sekunden)."""
    loc = datetime.fromtimestamp(now, tz)
    d = loc.date() + timedelta(days=0 if loc.hour < QUIET_HOUR else 1)
    return datetime(d.year, d.month, d.day, QUIET_HOUR, tzinfo=tz).timestamp()


# S1: Ruhe vorlaeufig im eigenen Eintrag; S2 -> ai_saga.set_quiet/quiet_until(uid, "today") (kc-srv)
def _quiet_until(d, now):
    try:
        q = float(d.get("q") or 0)
    except (TypeError, ValueError):
        return None
    return q if q > now else None


# --- Zeitzone -----------------------------------------------------------------------------------------------------
def _zone(c, uid, tz):
    from saga import tasksrc
    if isinstance(tz, str) and tz in tasksrc._zones():
        return ZoneInfo(tz)
    if isinstance(tz, ZoneInfo):
        return tz
    return tasksrc._acct_tz(c, uid, tasksrc.digest_cfg(c, uid))


def _zname(z):
    return getattr(z, "key", None) or str(z)


# --- Fakten (S1 vereinfacht: nur Eingang) --------------------------------------------------------------------------
def facts(c, uid, now, tz):
    day = datetime.fromtimestamp(now, tz).date().isoformat()
    rows = c.execute("SELECT id, text, due, prio FROM task WHERE uid=? AND done=0 AND due IS NOT NULL AND due <= ? "
                     "ORDER BY due, prio, created LIMIT 200", (uid, day)).fetchall()
    out = []
    for r in rows:
        due = str(r[2])[:10]
        kind = "over" if due < day else "due"
        out.append(Fact(kind, f"task:{r[0]}", 2 if kind == "over" else 3, {"title": r[1] or "", "prio": r[3]},
                        {"type": "task", "id": r[0]}))
    return out


def _short(s, n=TITLE_MAX):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


def render(fs, lang=None, persona=None):
    """S1: Teile mit t() (Zug "day" kommt in S2). -> {text, a11y, parts} oder None (ruhiger Tag)."""
    parts = []
    for kind, word in (("event", None), ("over", "überfällig"), ("due", "heute"), ("tomorrow", None)):
        mine = [f for f in fs if f.kind == kind]
        if not mine or word is None:
            continue
        first = mine[0]
        txt = t("„{title}“ " + word, title=_short(first.fields.get("title")))
        p = {"kind": kind, "text": txt, "target": first.target, "_a": txt}
        if len(mine) > 1:
            p.update(text=f"{txt} (+{len(mine) - 1})", n=len(mine), _a=txt + ", " + t("plus {n}", n=len(mine) - 1))
        parts.append(p)
    parts = parts[:PARTS_MAX]
    if not parts:
        return None
    a11y = t("Kurz gesagt") + ": " + ", ".join(p.pop("_a") for p in parts)
    return {"text": " · ".join(p["text"] for p in parts), "a11y": a11y, "parts": parts}


# --- Antwort ------------------------------------------------------------------------------------------------------
def _waiting(c, uid, persona):
    n = c.execute("SELECT count(*) FROM job WHERE uid=? AND state IN (?, ?)", (uid, *WAITING_STATES)).fetchone()[0] or 0
    if not n:
        return None
    sie = (persona or {}).get("address") == "sie"
    txt = t("{n} {n:Auftrag wartet|Aufträge warten} auf Sie ›" if sie else "{n} {n:Auftrag wartet|Aufträge warten} auf dich ›", n=n)
    return {"n": n, "text": txt, "target": {"type": "jobs", "group": "wartet_auf_dich"}}


def _daypart(h):
    return "morning" if 5 <= h < 11 else ("day" if 11 <= h < 18 else "evening")


def _sentence(uid, lang, persona, line, loc):
    from saga import ai_say
    import auth
    name = (str((auth.user_info(uid) or {}).get("name") or uid).split() or [uid])[0]
    day = (t("Heute: {list}.", list=", ".join(p["text"] for p in line["parts"])) if line else "")
    try:
        return ai_say.say("greet", {"person": name, "daypart": _daypart(loc.hour), "day": day}, lang, persona)
    except Exception:  # noqa: BLE001 - Satz ist Beigabe (K-G), die Zeile haengt nicht daran
        return None


def _v(d):
    return hashlib.sha256(json.dumps(d, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:12]


def compute(uid, tz=None, lang=None, now=None, conn=None):
    """Antwort von GET /api/ai/today (§13). Ohne Faehigkeit "ai": {"enabled": false}."""
    if conn is None:
        with _db() as c:
            return compute(uid, tz, lang, now, c)
    c = conn
    if not caps.can(uid, "ai", c):
        return {"enabled": False}
    from saga import ai_say, i18n
    lang = ai_say.lang_of(lang or i18n.get_lang())
    now = testclock.now() if now is None else now
    z = _zone(c, uid, tz)
    loc = datetime.fromtimestamp(now, z)
    st = _state(c, uid)
    on = not st.get("off")
    persona = ai_say.persona_for(uid, c)
    fs = facts(c, uid, now, z)
    line = render(fs, lang, persona)
    q = _quiet_until(st, now)
    quiet = ({"until": datetime.fromtimestamp(q, z).isoformat(timespec="seconds"), "text": t("Ruhe bis morgen"),
              "undo": t("Wieder an")} if q and on else None)
    ask = None
    if on and q and not st.get("asked") and len(_recent_x(st, now)) >= ASK_AFTER:
        ask = {"text": t("Tageszeile ausblenden?"), "yes": t("Ja, ausblenden"), "no": t("Nein, passt")}
    waiting = _waiting(c, uid, persona)
    counts = {"events": 0, "over": sum(1 for f in fs if f.kind == "over"), "due": sum(1 for f in fs if f.kind == "due"),
              "waiting": waiting["n"] if waiting else 0}
    out = {"enabled": True, "on": on, "day": loc.date().isoformat(), "tz": _zname(z), "label": t("Kurz gesagt"),
           "line": line if on and not quiet else None,
           "sentence": _sentence(uid, lang, persona, line, loc),
           "waiting": waiting, "counts": counts, "quiet": quiet, "ask": ask, "pending": [],
           "info": t("Ohne KI berechnet – aus deinen Aufgaben, Terminen und Aufträgen. Nichts davon geht an ein Sprachmodell.")}
    out["v"] = _v(out)
    return out


WIDGET_KEYS = ("enabled", "on", "line", "waiting", "counts", "quiet", "v")


def widget(uid, tz=None, lang=None):
    """Teil "line" fuer GET /api/app/today?line=1 (ohne sentence/ask/info)."""
    d = compute(uid, tz, lang)
    return {k: d[k] for k in WIDGET_KEYS if k in d}


# --- Schreiben ----------------------------------------------------------------------------------------------------
def set_prefs(uid, b, now=None):
    """PUT {on?, asked?} -> None oder Fehlertext. Falsche Werte vor jedem Schreiben."""
    if not isinstance(b, dict) or not b or set(b) - {"on", "asked"} or any(not isinstance(v, bool) for v in b.values()):
        return t("ungültige Angabe")
    with _db() as c, c:
        st = _state(c, uid)
        if "on" in b:
            st["off"] = not b["on"]
            if b["on"]:
                st.pop("q", None)
        if b.get("asked"):
            st["asked"] = True
        _save(c, uid, st)
    return None


def quiet(uid, on, tz=None, now=None):
    """X an der Zeile (on=True): Ruhe bis morgen 05:00 Ortszeit, Zeitpunkt merken; "Wieder an" (on=False)."""
    now = testclock.now() if now is None else now
    with _db() as c, c:
        st = _state(c, uid)
        if on:
            st["q"] = _next_five(now, _zone(c, uid, tz))
            st["x"] = (_recent_x(st, now) + [round(now, 3)])[-ASK_AFTER:]
        else:
            st.pop("q", None)
        _save(c, uid, st)


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
            return _bad(t("Der KI-Assistent ist für dich nicht eingeschaltet."), 403)
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
            return _bad(t("Der KI-Assistent ist für dich nicht eingeschaltet."), 403)
        quiet(uid, on, _tzq(request))
        return JSONResponse(compute(uid, _tzq(request)), headers={"Cache-Control": "no-store"})
    return await asyncio.to_thread(run)


@router.post("/api/ai/today/quiet")
async def api_today_quiet(request: Request):
    return await _quiet(request, True)


@router.delete("/api/ai/today/quiet")
async def api_today_quiet_off(request: Request):
    return await _quiet(request, False)

