"""Eigener Status (2.6, Bugs-DB 2029, Schritt 2029-1): Verfuegbar / Beschaeftigt / Nicht stoeren - je Konto, mit Ende,
kurzem Text und Herkunft. Vertrag fuer Apps: docs/APP-API.md ("Eigener Status").

Tabelle user_status (SCHEMA): uid, state available|busy|dnd, until (NULL = bis auf Weiteres; until <= jetzt heisst
available, die Ablauf-Schleife raeumt alle 30 s auf), text (hoechstens 80 Zeichen, nur bei source manual), source
manual|focus|system, since (seit wann dieser Zustand), updated, ver (steigt bei jeder Aenderung - Cache und
409 bei veraltetem Stand), prefs (Einstellungen, JSONB) und exc (Ausnahmen, JSONB). Keine Zeile = available mit
Standard-Einstellungen.

Endpunkte unter /api/account/status (GET /api/status ist der Dienst-Status des Dashboards, saga/dashboard.py, und bleibt so).

RT-29 (strukturell): Es gibt KEINE Spalte fuer den Namen eines Fokus der Apps (z. B. "Therapie"). Mit source focus wird
ein mitgeschickter Text verworfen; der Text steht nie im Log und nie in Fehlermeldungen.

Wirksamer Zustand: dnd (manuell) > Ruhezeit (geplantes Nicht stoeren, aus den Mitteilungs-Ruhezeiten user_kv 'push')
> in_call (Haken IN_CALL, Standard calls._busy_other - verdrahtet saga.calls beim Import) > busy > available.

reachability(c, uid, caller, kanal) ist die EINE Entscheidungsstelle fuer die naechsten Schritte: 2029-2 (Anrufe,
calls/fed_calls: route knock/busy/hold, chat_call.hold='dnd'), 2029-3 (Mitteilungen, push: push/sound, why 'quiet' =
ohne Sammelmeldung) und 2029-4 (Anzeige/Foederation: shown()). Sie rufen nur noch auf.

2029-3: saga.push fragt reachability() vor jeder Mitteilung (Haken push.REACH); das Ende eines Nicht stoeren meldet
bus "status.dnd_end" (set_status/expire_once, gleiche Transaktion) - daraus macht saga.push die Sammelmeldung status.digest.

Standards (Antworten des Maintainers stehen noch aus - je Punkt eine Konstante, je Konto ueber prefs umstellbar):
QUIET_DND, QUIET_DIGEST, IN_CALL_DEFAULT, SHOW_CALL, BUSY_MODE, REPEAT_ON, REPEAT_S, MENTION_BREAKS."""
import json
import re
import threading
from collections import namedtuple
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from db import connect as _db
from saga import bus, contacts, sharing, testclock
from saga.core import RESTORE_TEST, TZ, _api_guard, _bad, _body, _json_load, labels, resolve_input, t

router = APIRouter()

STATES = ("available", "busy", "dnd")
SOURCES = ("manual", "focus", "system")          # system: nur der Server (Ablauf); Apps senden manual|focus
API_SOURCES = ("manual", "focus")
EFFECTIVE = ("available", "busy", "in_call", "dnd")
KANAELE = ("call", "chat", "mention", "notify")  # Anruf, Chat-Nachricht, Erwaehnung (Seite/Chat), sonstige Mitteilung
ROUTES = ("normal", "knock", "busy", "hold")
TEXT_MAX = 80
EXC_MAX = 100
EXC_TYPES = ("user", "group")
EXC_RESERVED = ("contact", "e164")               # spaeter (Kontakte/Telefonie) - jetzt 400
UNTIL_MAX = 366 * 86400                          # Ende hoechstens ein Jahr voraus
DUR_MIN = 60
REPEAT_MIN, REPEAT_MAX = 30, 3600
TICK = 30.0                                      # Ablauf-Schleife (Sekunden)
CACHE_MAX = 5000

# --- Entscheidungsstellen (Standards nach den Empfehlungen, Bugs-DB 2029; je Konto ueber prefs umstellbar) ----------
QUIET_DND = True            # Ruhezeiten (Mitteilungen) zaehlen als geplantes Nicht stoeren (Ausnahmen/Wiederholung kommen durch)
QUIET_DIGEST = False        # ... aber ohne Sammelmeldung danach (2029-3 liest prefs.quiet_digest bzw. why 'quiet')
IN_CALL_DEFAULT = "knock"   # weitere Anrufe waehrend eines Anrufs: anklopfen (Rueckfall 'busy' = besetzt, baut 2029-2)
SHOW_CALL = True            # andere sehen "im Anruf" (2029-4, shown())
BUSY_MODE = "show"          # Beschaeftigt = nur Anzeige ('silent': Mitteilungen ohne Ton)
REPEAT_ON = True            # Wiederholungsanruf derselben Person kommt durch Nicht stoeren ...
REPEAT_S = 180              # ... innerhalb von so vielen Sekunden nach einem zurueckgehaltenen Anruf
MENTION_BREAKS = False      # Erwaehnungen durchbrechen Nicht stoeren nicht

DEF_PREFS = {"quiet_dnd": QUIET_DND, "quiet_digest": QUIET_DIGEST, "in_call": IN_CALL_DEFAULT, "show_call": SHOW_CALL,
             "busy": BUSY_MODE, "repeat": REPEAT_ON, "repeat_s": REPEAT_S, "mention_break": MENTION_BREAKS}
_PREF_OK = {"quiet_dnd": lambda v: isinstance(v, bool), "quiet_digest": lambda v: isinstance(v, bool),
            "in_call": lambda v: v in ("knock", "busy"), "show_call": lambda v: isinstance(v, bool),
            "busy": lambda v: v in ("show", "silent"), "repeat": lambda v: isinstance(v, bool),
            "repeat_s": lambda v: isinstance(v, int) and not isinstance(v, bool) and REPEAT_MIN <= v <= REPEAT_MAX,
            "mention_break": lambda v: isinstance(v, bool)}

# Haken (keine Zyklen): IN_CALL(c, uid, call_id) -> bool. saga.calls setzt beim Import calls._busy_other ein.
IN_CALL = None

Reach = namedtuple("Reach", "route push sound why")
"""route: normal (wie bisher) | knock (Anruf als Anklopfen) | busy (Anruf sofort besetzt) | hold (Anruf: nicht klingeln,
2029-2 merkt chat_call.hold='dnd'; chat/mention/notify: zustellen ohne Mitteilung). push/sound: Mitteilung bzw. Ton.
why: self | remote | available | busy | in_call | dnd | quiet | exception | repeat | mention."""

_CACHE = {}                 # uid -> ((ver, updated), push-Einstellung roh, geparst)
_RE_CTRL = re.compile(r"[\x00-\x1f\x7f  ]")


class Bad(ValueError):
    """Ungueltige Eingabe (Meldung ist ein uebersetzbarer Text ohne Eingabewerte)."""


# =====================================================================================================
# Lesen
# =====================================================================================================
def _parse(prefs, exc, push_raw):
    p = dict(DEF_PREFS)
    if isinstance(prefs, dict):
        p.update({k: v for k, v in prefs.items() if k in _PREF_OK and _PREF_OK[k](v)})
    users, groups = set(), set()
    for x in exc if isinstance(exc, list) else []:
        if isinstance(x, dict) and x.get("t") == "user" and isinstance(x.get("u"), str):
            users.add(x["u"])
        elif isinstance(x, dict) and x.get("t") == "group" and isinstance(x.get("g"), str):
            groups.add(x["g"])
    q = (_json_load(push_raw, {}) or {}).get("quiet") if push_raw else None
    quiet = None
    if isinstance(q, dict) and q.get("on") is True and _hhmm(q.get("from")) is not None and _hhmm(q.get("to")) is not None:
        quiet = {"from": _hhmm(q["from"]), "to": _hhmm(q["to"]), "tz": q.get("tz") if isinstance(q.get("tz"), str) else ""}
    return {"prefs": p, "users": frozenset(users), "groups": frozenset(groups), "exc": exc if isinstance(exc, list) else [],
            "quiet": quiet}


def _hhmm(s):
    if not isinstance(s, str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", s):
        return None
    return int(s[:2]) * 60 + int(s[3:])


_ZONES = {}


def _zone(name):
    if not name:
        return TZ
    z = _ZONES.get(name)
    if z is None:
        try:
            z = ZoneInfo(name)
        except Exception:  # noqa: BLE001 - unbekannte Zone: Zeitzone des Servers (wie push.quiet_now)
            z = TZ
        if len(_ZONES) < 1000:
            _ZONES[name] = z
    return z


def quiet_now(quiet, now):
    """Liegt now in den Ruhezeiten (Ortszeit der Person, ueber Mitternacht moeglich; wie push.quiet_now)?"""
    if not quiet:
        return False
    d = datetime.fromtimestamp(now, _zone(quiet["tz"]))
    m, f, to = d.hour * 60 + d.minute, quiet["from"], quiet["to"]
    if f == to:
        return False
    return f <= m < to if f < to else (m >= f or m < to)


def _load(c, uid):
    """-> (row|None, geparst). Eine Abfrage; prefs/exc nur neu lesen, wenn ver oder die Ruhezeiten sich geaendert haben."""
    r = c.execute("SELECT s.state, s.until, s.ver, s.text, s.source, s.since, s.updated, k.v FROM (SELECT ?::text AS u) x "
                  "LEFT JOIN user_status s ON s.uid=x.u LEFT JOIN user_kv k ON k.uid=x.u AND k.k='push'", (uid,)).fetchone()
    row = None if r is None or r[0] is None else {"state": r[0], "until": r[1], "ver": r[2], "text": r[3], "source": r[4],
                                                    "since": r[5], "updated": r[6]}
    # Schluessel ver + updated: ein zurueckgerollter Stand mit gleicher ver passt so nicht zu einem spaeteren
    ver, push_raw = ((row["ver"], row["updated"]) if row else (0, None)), (r[7] if r else None)
    hit = _CACHE.get(uid)
    if hit is not None and hit[0] == ver and hit[1] == push_raw:
        return row, hit[2]
    pe = c.execute("SELECT prefs, exc FROM user_status WHERE uid=?", (uid,)).fetchone() if row else None
    parsed = _parse(pe[0] if pe else None, pe[1] if pe else None, push_raw)
    if len(_CACHE) >= CACHE_MAX:
        _CACHE.clear()
    _CACHE[uid] = (ver, push_raw, parsed)
    return row, parsed


def _manual(row, now):
    """Gespeicherter Zustand, der jetzt gilt (abgelaufen = available)."""
    if not row or row["state"] not in STATES:
        return "available"
    if row["until"] is not None and row["until"] <= now:
        return "available"
    return row["state"]


def _in_call(c, uid, call=None):
    fn = IN_CALL
    if fn is None:
        return False
    cid = (call or {}).get("id") if isinstance(call, dict) else (call or "")
    return bool(fn(c, uid, cid or ""))


def _effective(c, uid, row, parsed, now, call=None, with_call=True, ic=None):
    """-> (wirksamer Zustand, Grund) - Grund: manual | quiet | call | none. ic: "im Anruf", falls schon bekannt."""
    m = _manual(row, now)
    if m == "dnd":
        return "dnd", "manual"
    if parsed["prefs"]["quiet_dnd"] and quiet_now(parsed["quiet"], now):
        return "dnd", "quiet"
    if with_call and (ic if ic is not None else _in_call(c, uid, call)):
        return "in_call", "call"
    if m == "busy":
        return "busy", "manual"
    return "available", "none"


def _excepted(c, parsed, caller):
    if not caller:
        return False
    if caller in parsed["users"]:
        return True
    return bool(parsed["groups"]) and bool(parsed["groups"] & sharing.groups_of(c, caller))


def _repeat(c, uid, caller, parsed, now, call=None):
    """Wiederholungsanruf: dieselbe Person hat innerhalb von repeat_s schon angerufen und wurde zurueckgehalten
    (chat_call.hold='dnd', setzt 2029-2)."""
    p = parsed["prefs"]
    if not p["repeat"] or not caller:
        return False
    cid = (call or {}).get("id") if isinstance(call, dict) else (call or "")
    return bool(c.execute("SELECT 1 FROM chat_call WHERE callee=? AND caller=? AND hold='dnd' AND created>=? AND created<=? "
                          "AND id<>? LIMIT 1", (uid, caller, now - p["repeat_s"], now, cid or "")).fetchone())


def reachability(c, uid, caller, kanal, now=None, call=None):
    """Darf caller uid ueber kanal jetzt erreichen, und wie? -> Reach(route, push, sound, why). call: der Anruf, um den es
    geht (dict mit id oder die Kennung) - zaehlt weder als "im Anruf" noch als frueherer Anruf."""
    if kanal not in KANAELE:
        raise ValueError(f"user_status: unbekannter Kanal {kanal!r}")
    if caller and caller == uid:
        return Reach("normal", True, True, "self")
    if not uid or sharing.is_remote(uid):
        return Reach("normal", True, True, "remote")       # Personen anderer Server entscheidet deren Server
    now = testclock.now() if now is None else now
    row, parsed = _load(c, uid)
    eff, why = _effective(c, uid, row, parsed, now, call, with_call=(kanal == "call"))
    p = parsed["prefs"]
    if eff == "dnd":
        if _excepted(c, parsed, caller):
            return Reach("normal", True, True, "exception")
        if kanal == "call" and _repeat(c, uid, caller, parsed, now, call):
            return Reach("normal", True, True, "repeat")
        if kanal == "mention" and p["mention_break"]:
            return Reach("normal", True, True, "mention")
        return Reach("hold", False, False, "quiet" if why == "quiet" else "dnd")
    if eff == "in_call":
        return Reach("busy" if p["in_call"] == "busy" else "knock", True, False, "in_call")
    if eff == "busy":
        return Reach("normal", True, p["busy"] != "silent", "busy")
    return Reach("normal", True, True, "available")


def quiet_plain(c, uid, now=None):
    """Ruhezeit jetzt, die NICHT als Nicht stoeren zaehlt (prefs.quiet_dnd aus) - fuer saga.push: dann gilt die alte
    Ruhezeit-Regel der Mitteilungen (keine Mitteilung, ohne Ausnahmen). Mit quiet_dnd entscheidet reachability()."""
    now = testclock.now() if now is None else now
    _, parsed = _load(c, uid)
    return not parsed["prefs"]["quiet_dnd"] and quiet_now(parsed["quiet"], now)


def _dnd_end(c, uid, since, end):
    """2029-3: ein Nicht stoeren (manuell/Fokus, nie Ruhezeit) ist zu Ende - in der Transaktion, die den Zustand aendert
    (Zeile per FOR UPDATE gesperrt, daher genau einmal). saga.push haengt sich an (Sammelmeldung status.digest)."""
    bus.fire("status.dnd_end", c, uid=uid, since=since, end=end)


def shown(c, uid, now=None):
    """Was andere sehen (Haken fuer 2029-4): {state: available|busy|in_call|dnd, until}. Ruhezeiten und Text nicht."""
    now = testclock.now() if now is None else now
    row, parsed = _load(c, uid)
    m = _manual(row, now)
    if m != "dnd" and parsed["prefs"]["show_call"] and _in_call(c, uid):
        return {"state": "in_call", "until": None}
    return {"state": m, "until": row["until"] if row and m != "available" else None}


# =====================================================================================================
# Ansicht (eigenes Konto)
# =====================================================================================================
def _exc_out(c, exc):
    users = [x["u"] for x in exc if x.get("t") == "user"]
    loc = [u for u in users if not sharing.is_remote(u)]
    nm = labels(c, loc) if loc else {}
    if any(sharing.is_remote(u) for u in users):
        nm.update(sharing.names(c, [u for u in users if sharing.is_remote(u)]))
    out = []
    for x in exc:
        if x.get("t") == "user":
            u = x["u"]
            ref = contacts.ref_of(u) or ""
            out.append({"type": "user", "ref": ref, "name": nm.get(u) or ""})
        elif x.get("t") == "group":
            out.append({"type": "group", "name": x["g"]})
    return out


def view(c, uid, now=None):
    now = testclock.now() if now is None else now
    row, parsed = _load(c, uid)
    m = _manual(row, now)
    ic = _in_call(c, uid)
    eff, why = _effective(c, uid, row, parsed, now, ic=ic)
    live = row is not None and m == row["state"]
    return {"state": m, "until": row["until"] if live and m != "available" else None,
            "text": row["text"] if live else None, "source": row["source"] if live else "system",
            "since": row["since"] if live else (row["until"] if row and row["until"] else None),
            "updated": row["updated"] if row else None, "ver": row["ver"] if row else 0,
            "effective": eff, "why": why, "quiet": quiet_now(parsed["quiet"], now), "in_call": ic,
            "prefs": dict(parsed["prefs"]), "exceptions": _exc_out(c, parsed["exc"]),
            "limits": {"text": TEXT_MAX, "exceptions": EXC_MAX, "repeat_s": [REPEAT_MIN, REPEAT_MAX], "until_max": UNTIL_MAX,
                       "dur_min": DUR_MIN},
            "now": round(now, 3)}


def _changed(c, uid):
    """Live-Ereignis status.changed an die eigenen Verbindungen (nach dem Commit)."""
    bus.emit_after(c, "direct", {"uids": [uid], "typ": "status.changed", "data": view(c, uid)})


# =====================================================================================================
# Schreiben
# =====================================================================================================
def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) < 1e11


def _clean_text(v):
    if v is None:
        return None
    if not isinstance(v, str):
        raise Bad(t("ungültige Angabe"))
    s = _RE_CTRL.sub(" ", v).strip()
    if len(s) > TEXT_MAX:
        raise Bad(t("Statustext: höchstens 80 Zeichen"))
    return s or None


def set_status(c, uid, b, now=None):
    """PUT /api/account/status in der Transaktion von c -> (view, code). b: {state, until?, dur?, text?, source?, ver?}."""
    now = testclock.now() if now is None else now
    if not isinstance(b, dict) or any(k not in ("state", "until", "dur", "text", "source", "ver") for k in b):
        raise Bad(t("ungültige Angabe"))
    st = b.get("state")
    if st not in STATES:
        raise Bad(t("Status: verfügbar, beschäftigt oder nicht stören angeben"))
    src = b.get("source", "manual")
    if src not in API_SOURCES:
        raise Bad(t("ungültige Angabe"))
    if "until" in b and "dur" in b:
        raise Bad(t("ungültige Angabe"))
    until = None
    if b.get("until") is not None:
        if not _num(b["until"]):
            raise Bad(t("ungültige Angabe"))
        until = float(b["until"])
    elif b.get("dur") is not None:
        if not _num(b["dur"]) or b["dur"] < DUR_MIN:
            raise Bad(t("ungültige Angabe"))
        until = now + float(b["dur"])
    if until is not None and (until <= now or until > now + UNTIL_MAX):
        raise Bad(t("Status: Ende muss in der Zukunft liegen (höchstens ein Jahr)"))
    if st == "available":
        until = None
    text = _clean_text(b.get("text")) if src == "manual" else None      # RT-29: mit Fokus nie ein Text (auch kein Name)
    if "ver" in b and (not isinstance(b["ver"], int) or isinstance(b["ver"], bool)):
        raise Bad(t("ungültige Angabe"))
    cur = c.execute("SELECT state, until, source, ver, since FROM user_status WHERE uid=? FOR UPDATE", (uid,)).fetchone()
    if "ver" in b and b["ver"] != (cur[3] if cur else 0):
        return view(c, uid, now), 409
    if cur:
        m = _manual({"state": cur[0], "until": cur[1]}, now)
        if src == "focus" and cur[2] == "manual" and m != "available" and st == "available":
            return dict(view(c, uid, now), kept=True), 200     # Fokus-Ende hebt einen selbst gesetzten Status nicht auf
        since = cur[4] if m == st and cur[0] == st else now
        c.execute("UPDATE user_status SET state=?, until=?, text=?, source=?, since=?, updated=?, ver=ver+1 WHERE uid=?",
                  (st, until, text, src, since, now, uid))
        if cur[0] == "dnd" and (m != "dnd" or st != "dnd"):    # Nicht stoeren endet jetzt (bzw. war schon abgelaufen)
            _dnd_end(c, uid, cur[4], now if m == "dnd" else cur[1])
    else:
        c.execute("INSERT INTO user_status (uid, state, until, text, source, since, updated, ver) VALUES (?,?,?,?,?,?,?,1) "
                  "ON CONFLICT (uid) DO UPDATE SET state=EXCLUDED.state, until=EXCLUDED.until, text=EXCLUDED.text, "
                  "source=EXCLUDED.source, since=EXCLUDED.since, updated=EXCLUDED.updated, ver=user_status.ver+1",
                  (uid, st, until, text, src, now, now))
    _changed(c, uid)
    return view(c, uid, now), 200


def _exc_in(c, uid, items):
    if not isinstance(items, list):
        raise Bad(t("ungültige Angabe"))
    if len(items) > EXC_MAX:
        raise Bad(t("Ausnahmen: höchstens 100"))
    mine = None
    out, seen = [], set()
    for x in items:
        if not isinstance(x, dict) or not isinstance(x.get("type"), str):
            raise Bad(t("ungültige Angabe"))
        typ = x["type"]
        if typ in EXC_RESERVED:
            raise Bad(t("Ausnahmen: diese Art ist für später reserviert"))
        if typ not in EXC_TYPES or any(k not in ("type", "ref", "name") for k in x):
            raise Bad(t("ungültige Angabe"))
        if typ == "user":
            ref = x.get("ref")
            if not isinstance(ref, str) or not ref or len(ref) > 300:
                raise Bad(t("ungültige Angabe"))
            if contacts.is_remote_ref(ref):
                u = "@" + ref
            else:
                u = resolve_input(c, uid, ref)           # Handle; Anmeldename nur nach den Regeln der Personensuche
                if not u or sharing.is_remote(u) or not c.execute("SELECT 1 FROM users WHERE username=?", (u,)).fetchone():
                    raise Bad(t("Ausnahmen: unbekannte Person oder Gruppe"))
            if u == uid:
                continue
            key = ("user", u)
            item = {"t": "user", "u": u}
        else:
            g = x.get("name")
            if mine is None:
                mine = sharing.groups_of(c, uid)
            if not isinstance(g, str) or g not in mine:
                raise Bad(t("Ausnahmen: unbekannte Person oder Gruppe"))
            key = ("group", g)
            item = {"t": "group", "g": g}
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def set_prefs(c, uid, b, now=None):
    """PUT /api/account/status/prefs in der Transaktion von c -> view. b: Teil von prefs, dazu exceptions (ganze Liste)."""
    now = testclock.now() if now is None else now
    if not isinstance(b, dict) or not b or any(k not in _PREF_OK and k != "exceptions" for k in b):
        raise Bad(t("ungültige Angabe"))
    for k, v in b.items():
        if k != "exceptions" and not _PREF_OK[k](v):
            raise Bad(t("ungültige Angabe"))
    exc = _exc_in(c, uid, b["exceptions"]) if "exceptions" in b else None
    cur = c.execute("SELECT prefs, exc FROM user_status WHERE uid=? FOR UPDATE", (uid,)).fetchone()
    p = dict(cur[0]) if cur and isinstance(cur[0], dict) else {}
    p.update({k: v for k, v in b.items() if k != "exceptions"})
    p = {k: v for k, v in p.items() if k in _PREF_OK}
    e = exc if exc is not None else (cur[1] if cur and isinstance(cur[1], list) else [])
    if cur:
        c.execute("UPDATE user_status SET prefs=?::jsonb, exc=?::jsonb, updated=?, ver=ver+1 WHERE uid=?",
                  (json.dumps(p), json.dumps(e), now, uid))
    else:
        c.execute("INSERT INTO user_status (uid, state, source, since, updated, ver, prefs, exc) "
                  "VALUES (?, 'available', 'system', ?, ?, 1, ?::jsonb, ?::jsonb) ON CONFLICT (uid) DO UPDATE SET "
                  "prefs=EXCLUDED.prefs, exc=EXCLUDED.exc, updated=EXCLUDED.updated, ver=user_status.ver+1",
                  (uid, now, now, json.dumps(p), json.dumps(e)))
    _changed(c, uid)
    return view(c, uid, now)


def expire_once(now=None):
    """Abgelaufene Status (until <= jetzt) auf available setzen und melden -> Anzahl."""
    now = testclock.now() if now is None else now
    with _db() as c, c:
        rows = c.execute("SELECT uid, state, since, until FROM user_status WHERE until IS NOT NULL AND until<=? AND state<>'available' "
                         "ORDER BY until LIMIT 500 FOR UPDATE SKIP LOCKED", (now,)).fetchall()
        for u, st, since, until in rows:
            c.execute("UPDATE user_status SET state='available', until=NULL, text=NULL, source='system', since=until, updated=?, "
                      "ver=ver+1 WHERE uid=?", (now, u))
            if st == "dnd":
                _dnd_end(c, u, since, until)
            _changed(c, u)
    return len(rows)


def drop_account(c, uid):
    """Kontoloeschung: die eigene Zeile geht mit USER_TABLES; hier die Person aus den Ausnahmen anderer entfernen (ein
    spaeter gleichnamiges Konto erbt keine Ausnahme)."""
    n = 0
    for u, exc in c.execute("SELECT uid, exc FROM user_status WHERE exc @> ?::jsonb",
                            (json.dumps([{"t": "user", "u": uid}]),)).fetchall():
        keep = [x for x in exc if not (isinstance(x, dict) and x.get("t") == "user" and x.get("u") == uid)]
        n += c.execute("UPDATE user_status SET exc=?::jsonb, ver=ver+1 WHERE uid=?", (json.dumps(keep), u)).rowcount
    _CACHE.clear()
    return n


# --- Persoenliches Backup ----------------------------------------------------------------------------------------------
def export(c, uid):
    """Einstellungen und Ausnahmen (Personen als Handle, nie Anmeldename); der Zustand selbst ist voruebergehend."""
    r = c.execute("SELECT prefs, exc FROM user_status WHERE uid=?", (uid,)).fetchone()
    if not r:
        return None
    parsed = _parse(r[0], r[1], None)
    return {"prefs": {k: v for k, v in parsed["prefs"].items() if (r[0] or {}).get(k) is not None},
            "exceptions": [{"type": x["type"], **({"ref": x["ref"]} if x["type"] == "user" else {"name": x["name"]})}
                           for x in _exc_out(c, parsed["exc"]) if x["type"] == "group" or x.get("ref")]}


def restore(c, uid, data):
    """Aus einem Backup: nur, wenn das Konto noch keine Status-Einstellungen hat; Unbekanntes wird verworfen. -> 0/1"""
    if not isinstance(data, dict) or c.execute("SELECT 1 FROM user_status WHERE uid=? AND (prefs<>'{}'::jsonb OR exc<>'[]'::jsonb)",
                                               (uid,)).fetchone():
        return 0                                  # eine Zeile nur mit Zustand (ohne Einstellungen) zaehlt nicht als vorhanden
    p = {k: v for k, v in (data.get("prefs") or {}).items() if k in _PREF_OK and _PREF_OK[k](v)} \
        if isinstance(data.get("prefs"), dict) else {}
    exc = []
    for x in (data.get("exceptions") or [])[:EXC_MAX] if isinstance(data.get("exceptions"), list) else []:
        try:
            if _exc_in(c, uid, [x]):
                exc.append(x)
        except Bad:
            continue
    if not p and not exc:
        return 0
    set_prefs(c, uid, dict(p, exceptions=exc))
    return 1


# =====================================================================================================
# Endpunkte
# =====================================================================================================
def _no_store(d, code=200):
    return JSONResponse(d, status_code=code, headers={"Cache-Control": "no-store"})


def _get(uid):
    with _db() as c:
        return view(c, uid)


def _put(uid, b, fn):
    try:
        with _db() as c, c:
            out = fn(c, uid, b)
    except Bad as ex:
        return _bad(str(ex))
    if isinstance(out, tuple):
        v, code = out
        return _no_store(dict(v, error=t("Der Status wurde inzwischen geändert – bitte neu laden.")) if code == 409 else v, code)
    return _no_store(out)


@router.get("/api/account/status")
async def api_get(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    return _no_store(await run_in_threadpool(_get, uid))


@router.put("/api/account/status")
async def api_put(request: Request):
    """{state: available|busy|dnd, until?: ts|null, dur?: s, text?: str|null (nur manual), source?: manual|focus, ver?}"""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request)
    if b is None:
        return _bad(t("ungültige Angabe"))
    return await run_in_threadpool(_put, uid, b, set_status)


@router.put("/api/account/status/prefs")
async def api_put_prefs(request: Request):
    """{quiet_dnd?, quiet_digest?, in_call?: knock|busy, show_call?, busy?: show|silent, repeat?, repeat_s?, mention_break?,
    exceptions?: [{type: user, ref}|{type: group, name}]}"""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request)
    if b is None:
        return _bad(t("ungültige Angabe"))
    return await run_in_threadpool(_put, uid, b, set_prefs)


# =====================================================================================================
# Ablauf-Schleife
# =====================================================================================================
_wake = threading.Event()
_thread = {"t": None, "stop": False}


def _loop():
    while not _thread["stop"]:
        try:
            expire_once()
        except Exception as ex:  # noqa: BLE001 - Datenbank kurz weg: naechster Durchlauf
            print(f"user_status: {type(ex).__name__}", flush=True)
        _wake.wait(TICK)
        _wake.clear()


def start():
    if RESTORE_TEST:
        return
    if _thread["t"] is None or not _thread["t"].is_alive():
        _thread["stop"] = False
        _thread["t"] = threading.Thread(target=_loop, name="saga-status", daemon=True)
        _thread["t"].start()


def stop():
    _thread["stop"] = True
    _wake.set()
    _thread["t"] = None


testclock.on_tick(expire_once)                   # /_test/tick: Ablauf sofort (nur mit Testhaken)
