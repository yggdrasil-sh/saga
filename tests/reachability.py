"""Eigener Status: reachability() (2.6, Bugs-DB 2029-1, saga/user_status.py) im Prozess gegen die Datenbank aus
DATABASE_URL, Zeit ueber die Testuhr (testclock.set_clock, angehalten).

Prueft: Matrix Zustand (available/busy/dnd/abgelaufen/Ruhezeit) x im Anruf x Kanal (call/chat/mention/notify) x
Anrufende (normal/Ausnahme Person/Ausnahme Gruppe/selbst) -> Reach(route, push, sound, why) · Rangfolge dnd > Ruhezeit >
in_call > busy > available · until <= jetzt = available, Ablauf ueber expire_once und den Takt-Haken (Testuhr) mit
Ereignis status.changed · Wiederholungsanruf aus chat_call.hold='dnd' bei 179/180/181 s (Standard 180), Anruf selbst
zaehlt nicht, andere Person/ohne hold nicht, repeat aus, repeat_s 300 · Ausnahmen (Person, Gruppe, Person anderer
Servers, hoechstens 100, contact/e164 reserviert, unbekannt, fremde Gruppe, sich selbst, doppelt) · Namen foederierter
Ausnahmen (fed_directory > chat_contact > fed_person, mit Herkunft, sonst leer; 2391) · Ruhezeit ueber
Mitternacht in zwei Zeitzonen, quiet_dnd aus · Weitere Anrufe knock/busy, Anruf selbst zaehlt nicht als "im Anruf",
shown() mit show_call · Erwaehnungen (mention_break) · Beschaeftigt nur Anzeige / silent · Cache je uid ueber ver
(eine Abfrage im Treffer, Aenderung von prefs und Ruhezeiten sofort sichtbar) · Kontoloeschung (Zeile weg, aus Ausnahmen
anderer entfernt) · Backup-Export (Handles, keine Anmeldenamen) und Einspielen · Perf (limits.json "Status: reachability").
Wegwerf-Konten rs-*, Gruppe rs-team; stellt die Testuhr wieder her.
Aufruf (saga-test): PYTHONPATH=tests/pgshim:. python tests/reachability.py"""
import json
import os
import statistics
import sys
import time

import db

db.wait_ready()
import auth  # noqa: E402
from db import connect  # noqa: E402
from saga import bus, calls, testclock, user_status as US  # noqa: E402,F401  (calls verdrahtet IN_CALL)
from saga.core import local_ref  # noqa: E402

A, B, C, D = "rs-anna", "rs-bert", "rs-cora", "rs-dora"
GRP = "rs-team"
T0 = 1893456000.0                       # 2030-01-01 00:00 UTC (01:00 Berlin, 31.12. 19:00 New York)
REMOTE = "@~abcdefghijklmnop@b.example"
res = []
EVENTS = []


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, "" if c else str(info)[:400], flush=True)


def clock(t):
    testclock.set_clock({"freeze": True})
    testclock.set_clock({"set": t})


def R(uid, caller, kanal, call=None, now=None):
    with connect() as c:
        return tuple(US.reachability(c, uid, caller, kanal, now=now, call=call))


def put(uid, **b):
    with connect() as c, c:
        return US.set_status(c, uid, b)


def prefs(uid, **b):
    with connect() as c, c:
        return US.set_prefs(c, uid, b)


def bad(fn, *a, **kw):
    try:
        fn(*a, **kw)
        return False
    except US.Bad:
        return True


def quiet(uid, on=True, f="22:00", to="07:00", tz=""):
    with connect() as c, c:
        c.execute("INSERT INTO user_kv (uid, k, v) VALUES (?, 'push', ?) ON CONFLICT (uid, k) DO UPDATE SET v=EXCLUDED.v",
                  (uid, json.dumps({"quiet": {"on": on, "from": f, "to": to, "tz": tz}})))


def reset(uid):
    with connect() as c, c:
        c.execute("DELETE FROM user_status WHERE uid=?", (uid,))
        c.execute("DELETE FROM user_kv WHERE uid=? AND k='push'", (uid,))
        c.execute("DELETE FROM chat_call WHERE caller LIKE 'rs-%' OR callee LIKE 'rs-%'")


_CHAT = {}


def chat_id():
    if "id" not in _CHAT:
        with connect() as c, c:
            _CHAT["id"] = c.execute("INSERT INTO chat (kind, dm_key, created_by, created) VALUES ('dm', ?, ?, ?)",
                                    (f"{A}|{B}", A, T0)).lastrowid
    return _CHAT["id"]


def call_row(cid, caller, callee, created, state="ended", hold=None, kind="dm"):
    with connect() as c, c:
        c.execute("INSERT INTO chat_call (id, chat_id, caller, callee, caller_device, media, state, created, kind, hold) "
                  "VALUES (?,?,?,?,'d1','audio',?,?,?,?)", (cid, chat_id(), caller, callee, state, created, kind, hold))


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'rs-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM chat WHERE dm_key LIKE 'rs-%' OR created_by LIKE 'rs-%'")
        c.execute("DELETE FROM user_status WHERE uid LIKE 'rs-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'rs-%'")
        c.execute("DELETE FROM groups WHERE name LIKE 'rs-%'")
    _CHAT.clear()


# --- Matrix --------------------------------------------------------------------------------------------------------
def part_matrix():
    reset(A)
    clock(T0)
    prefs(A, exceptions=[{"type": "user", "ref": local_ref(B)}, {"type": "group", "name": GRP}])
    N = ("normal", True, True)
    HOLD = ("hold", False, False)
    # (Zustand, im Anruf, Kanal, Anrufende) -> erwartet (route, push, sound, why)
    cases = []
    for k in US.KANAELE:
        cases += [("available", False, k, D, N + ("available",)),
                  ("busy", False, k, D, N + ("busy",)),
                  ("dnd", False, k, D, HOLD + ("dnd",)),
                  ("dnd", False, k, B, N + ("exception",)),
                  ("dnd", False, k, C, N + ("exception",)),
                  ("dnd", False, k, A, N + ("self",)),
                  ("expired", False, k, D, N + ("available",)),
                  ("quiet", False, k, D, HOLD + ("quiet",)),
                  ("quiet", False, k, C, N + ("exception",))]
        # im Anruf: nur fuer Anrufe wirksam (andere Kanaele unveraendert); dnd/Ruhezeit stehen darueber
        cases += [("available", True, k, D, ("knock", True, False, "in_call") if k == "call" else N + ("available",)),
                  ("busy", True, k, D, ("knock", True, False, "in_call") if k == "call" else N + ("busy",)),
                  ("dnd", True, k, D, HOLD + ("dnd",)),
                  ("quiet", True, k, D, HOLD + ("quiet",)),
                  ("dnd", True, k, B, N + ("exception",))]
    wrong = []
    for st, inc, k, who, exp in cases:
        with connect() as c, c:
            c.execute("DELETE FROM chat_call WHERE caller LIKE 'rs-%' OR callee LIKE 'rs-%'")
        quiet(A, on=(st == "quiet"), tz="Europe/Berlin")             # T0 = 01:00 Berlin: in 22:00-07:00
        if st in ("available", "busy", "dnd", "quiet"):
            put(A, state="available" if st == "quiet" else st)
        else:                                                        # abgelaufen: dnd bis T0-1, Schleife noch nicht gelaufen
            put(A, state="dnd", until=T0 + 60)
            clock(T0 + 61)
        if inc:
            call_row("rs-live", B, A, testclock.now() - 5, state="active")
        got = R(A, who, k)
        if got != exp:
            wrong.append((st, inc, k, who, got, exp))
        clock(T0)
    ok(f"Matrix: {len(cases)} Faelle Zustand x im Anruf x Kanal x Anrufende", not wrong, wrong[:6])
    reset(A)


def part_rank_and_misc():
    reset(A)
    clock(T0)
    put(A, state="dnd")
    call_row("rs-live", B, A, T0 - 5, state="active")
    with connect() as c:
        v = US.view(c, A)
    ok(f"Rangfolge: dnd vor in_call (effective {v['effective']}, in_call {v['in_call']})", v["effective"] == "dnd" and v["in_call"] is True)
    put(A, state="busy")
    with connect() as c:
        v = US.view(c, A)
    ok(f"Rangfolge: in_call vor busy ({v['effective']}, why {v['why']})", v["effective"] == "in_call" and v["why"] == "call")
    ok("Anruf selbst zaehlt nicht als im Anruf (call={'id'})", R(A, B, "call", call={"id": "rs-live"}) == ("normal", True, True, "busy"))
    ok("... auch als Kennung", R(A, B, "call", call="rs-live") == ("normal", True, True, "busy"))
    prefs(A, in_call="busy")
    ok("weitere Anrufe: in_call=busy -> route busy (Rueckfall fuer 2029-2)", R(A, D, "call") == ("busy", True, False, "in_call"))
    prefs(A, in_call="knock")
    with connect() as c:
        s1 = US.shown(c, A)
    prefs(A, show_call=False)
    with connect() as c:
        s2 = US.shown(c, A)
    ok(f"shown(): im Anruf sichtbar mit show_call, sonst der eigene Zustand ({s1}, {s2})",
       s1["state"] == "in_call" and s2["state"] == "busy")
    prefs(A, show_call=True)
    with connect() as c, c:
        c.execute("DELETE FROM chat_call WHERE id='rs-live'")
    prefs(A, busy="silent")
    ok("Beschaeftigt silent: zustellen ohne Ton", R(A, D, "chat") == ("normal", True, False, "busy"))
    prefs(A, busy="show")
    ok("Beschaeftigt (Standard): nur Anzeige", R(A, D, "chat") == ("normal", True, True, "busy"))
    put(A, state="dnd")
    ok("Erwaehnung durchbricht dnd nicht (Standard)", R(A, D, "mention") == ("hold", False, False, "dnd"))
    prefs(A, mention_break=True)
    ok("mention_break: Erwaehnung kommt durch, Chat nicht", R(A, D, "mention") == ("normal", True, True, "mention") and
       R(A, D, "chat")[0] == "hold")
    prefs(A, mention_break=False)
    ok("Person anderer Servers als uid: remote", R(REMOTE, D, "call") == ("normal", True, True, "remote"))
    ok("ohne Anrufende (System): dnd haelt", R(A, None, "notify") == ("hold", False, False, "dnd"))
    try:
        R(A, D, "fax")
        ok("unbekannter Kanal -> ValueError", False)
    except ValueError:
        ok("unbekannter Kanal -> ValueError", True)
    ok("Standards nach den Empfehlungen (prefs ohne Zeile)", US.DEF_PREFS == {
        "quiet_dnd": True, "quiet_digest": False, "in_call": "knock", "show_call": True, "busy": "show", "repeat": True,
        "repeat_s": 180, "mention_break": False}, US.DEF_PREFS)
    reset(A)


# --- Ablauf ----------------------------------------------------------------------------------------------------------
def part_expiry():
    reset(A)
    clock(T0)
    EVENTS.clear()
    v = put(A, state="dnd", until=T0 + 600, text="Konzentriert")[0]
    ok(f"dnd bis T0+600 (ver {v['ver']}, until {v['until']})", v["state"] == "dnd" and v["until"] == T0 + 600 and v["ver"] == 1)
    ok("Live: status.changed an das eigene Konto nach dem Commit",
       any(e[0] == "direct" and e[1]["typ"] == "status.changed" and e[1]["uids"] == [A] for e in EVENTS), EVENTS[-1:])
    clock(T0 + 599)
    ok("T0+599: haelt noch", R(A, D, "call")[0] == "hold")
    clock(T0 + 600)
    ok("T0+600 (until <= jetzt): available, auch ohne Schleife", R(A, D, "call") == ("normal", True, True, "available"))
    with connect() as c:
        v = US.view(c, A)
    ok(f"Ansicht nach Ablauf: available, kein Text, source system ({v['state']}, {v['text']}, {v['source']})",
       v["state"] == "available" and v["text"] is None and v["until"] is None and v["source"] == "system")
    EVENTS.clear()
    r = testclock._run(testclock._TICK)
    with connect() as c:
        row = c.execute("SELECT state, until, text, source, ver FROM user_status WHERE uid=?", (A,)).fetchone()
    ok(f"Takt-Haken (/_test/tick) setzt zurueck ({r}, {row})", r["ran"] >= 1 and row[0] == "available" and row[1] is None
       and row[2] is None and row[3] == "system" and row[4] == 2)
    ok("... und meldet status.changed", any(e[1].get("typ") == "status.changed" and e[1]["data"]["state"] == "available"
                                            for e in EVENTS if e[0] == "direct"))
    ok("zweiter Lauf: nichts mehr faellig", US.expire_once() == 0)
    put(A, state="busy", dur=3600)
    clock(T0 + 600 + 3600)
    ok("dur: Ende = jetzt + dur, expire_once direkt", US.expire_once() == 1)
    with connect() as c, c:                              # Pruefer: Last - hoechstens 500 je Lauf, der Rest im naechsten
        c.executemany("INSERT INTO user_status (uid, state, until, source, since, updated) VALUES (?, 'dnd', ?, 'manual', ?, ?)",
                      [(f"rs-x{i:03d}", testclock.now() - 1 - i, T0, T0) for i in range(501)])
    n1, n2 = US.expire_once(), US.expire_once()
    with connect() as c, c:
        c.execute("DELETE FROM user_status WHERE uid LIKE 'rs-x%'")
    ok(f"Ablauf: hoechstens 500 je Lauf ({n1}, dann {n2})", (n1, n2) == (500, 1))
    reset(A)


# --- Wiederholung -----------------------------------------------------------------------------------------------------
def part_repeat():
    reset(A)
    clock(T0)
    put(A, state="dnd")
    for age, want in ((179, "repeat"), (180, "repeat"), (181, "dnd")):
        with connect() as c, c:
            c.execute("DELETE FROM chat_call WHERE caller LIKE 'rs-%' OR callee LIKE 'rs-%'")
        call_row("rs-c1", B, A, T0 - age, hold="dnd")
        got = R(A, B, "call")
        ok(f"Wiederholungsanruf nach {age} s: {got[3]}", got[3] == want, got)
    with connect() as c, c:
        c.execute("DELETE FROM chat_call WHERE caller LIKE 'rs-%' OR callee LIKE 'rs-%'")
    call_row("rs-c1", B, A, T0 - 60, hold="dnd")
    ok("Wiederholung nur fuer Anrufe (Chat haelt)", R(A, B, "chat")[0] == "hold")
    ok("andere Person: kein Wiederholungsanruf", R(A, D, "call")[3] == "dnd")
    ok("der Anruf selbst zaehlt nicht als frueherer", R(A, B, "call", call={"id": "rs-c1"})[3] == "dnd")
    call_row("rs-c2", D, A, T0 - 60, hold=None)
    ok("frueherer Anruf ohne hold='dnd' zaehlt nicht", R(A, D, "call")[3] == "dnd")
    prefs(A, repeat=False)
    ok("repeat aus: haelt", R(A, B, "call")[3] == "dnd")
    prefs(A, repeat=True, repeat_s=300)
    with connect() as c, c:
        c.execute("UPDATE chat_call SET created=? WHERE id='rs-c1'", (T0 - 299,))
    ok("repeat_s 300: nach 299 s durch", R(A, B, "call")[3] == "repeat")
    quiet(A, tz="Europe/Berlin")
    put(A, state="available")
    ok("Ruhezeit: Wiederholungsanruf kommt durch", R(A, B, "call") == ("normal", True, True, "repeat"))
    ok("Ruhezeit: andere halten (why quiet = ohne Sammelmeldung)", R(A, D, "call") == ("hold", False, False, "quiet"))
    reset(A)


# --- Ausnahmen --------------------------------------------------------------------------------------------------------
def part_exceptions():
    reset(A)
    clock(T0)
    put(A, state="dnd")
    rr = REMOTE[1:]
    v = prefs(A, exceptions=[{"type": "user", "ref": local_ref(B)}, {"type": "user", "ref": local_ref(B)},
                             {"type": "user", "ref": rr}, {"type": "group", "name": GRP}, {"type": "user", "ref": local_ref(A)}])
    ex = v["exceptions"]
    ok(f"Ausnahmen: doppelt und sich selbst entfallen ({ex})", [x["type"] for x in ex] == ["user", "user", "group"])
    ok("Ausnahmen als Handle, nie Anmeldename", ex[0]["ref"] == local_ref(B) and ex[1]["ref"] == rr and B not in json.dumps(v))
    ok("Person anderer Servers als Ausnahme", R(A, REMOTE, "call")[3] == "exception")
    ok("Gruppenmitglied (sharing.groups_of) als Ausnahme", R(A, C, "chat")[3] == "exception")
    ok("Nicht-Mitglied haelt", R(A, D, "chat")[3] == "dnd")
    ok("101 Ausnahmen -> abgelehnt", bad(prefs, A, exceptions=[{"type": "group", "name": GRP}] * 101))
    ok("100 Ausnahmen -> angenommen (doppelt zusammengefasst)", not bad(prefs, A, exceptions=[{"type": "group", "name": GRP}] * 100))
    for typ in ("contact", "e164"):
        ok(f"Art {typ} reserviert -> abgelehnt", bad(prefs, A, exceptions=[{"type": typ, "ref": "x"}]))
    ok("unbekannte Art -> abgelehnt", bad(prefs, A, exceptions=[{"type": "role", "name": "x"}]))
    ok("unbekannter Handle -> abgelehnt", bad(prefs, A, exceptions=[{"type": "user", "ref": "~aaaaaaaaaaaaaaaa"}]))
    ok("fremde Gruppe (nicht Mitglied) -> abgelehnt", bad(prefs, A, exceptions=[{"type": "group", "name": "rs-fremd"}]))
    ok("Zusatzfeld -> abgelehnt", bad(prefs, A, exceptions=[{"type": "group", "name": GRP, "x": 1}]))
    ok("Liste ist Pflichtform", bad(prefs, A, exceptions={"type": "group"}))
    prefs(A, exceptions=[])
    ok("leere Liste: keine Ausnahmen mehr", R(A, C, "chat")[3] == "dnd")
    reset(A)


# --- Namen foederierter Ausnahmen (Bugs-DB 2391) ------------------------------------------------------------------------
FH, FPID = "rs-fed.example", 990
FX = {k: "~" + k * 16 for k in "abcdefg"}


def fed_clean(c):
    c.execute("DELETE FROM fed_directory WHERE peer_id=?", (FPID,))
    c.execute("DELETE FROM fed_peer WHERE id=?", (FPID,))
    c.execute("DELETE FROM fed_person WHERE principal LIKE ?", ("%@" + FH,))
    c.execute("DELETE FROM chat_contact WHERE ref LIKE ?", ("%@" + FH,))


def part_fed_names():
    reset(A)
    with connect() as c, c:
        fed_clean(c)
        c.execute("INSERT INTO fed_peer (id, host, base_url, public_key, fingerprint, status, created) VALUES (?,?,?,?,?,?,?)",
                  (FPID, FH, "https://" + FH, "x", "x", "active", T0))
        for k, nm in (("a", "Bert Gast"), ("e", FX["e"]), ("f", "")):             # e: Kennung als Name zaehlt nicht
            c.execute("INSERT INTO fed_directory (peer_id, handle, name, updated) VALUES (?,?,?,?)", (FPID, FX[k], nm, T0))
        for uid, k, nm in ((A, "a", "Alter Chatname"), (A, "b", "Cora Chat"), (A, "e", "Eva Chat"), (B, "d", "Nur bei Bert")):
            c.execute("INSERT INTO chat_contact (uid, ref, name, first_at, last_at) VALUES (?,?,?,?,?)", (uid, f"{FX[k]}@{FH}", nm, T0, T0))
        c.execute("INSERT INTO fed_person (principal, peer_id, display, updated) VALUES (?,?,?,?)", (f"@{FX['c']}@{FH}", FPID, "Dora Person", T0))
    refs = [f"{FX[k]}@{FH}" for k in "abcdef"] + [f"{FX['g']}@andere.example"]
    v = prefs(A, exceptions=[{"type": "user", "ref": r} for r in refs] + [{"type": "user", "ref": local_ref(B)}])
    nm = {x["ref"]: x["name"] for x in v["exceptions"]}
    ok(f"Adressbuch der verbundenen Instanz zuerst, mit Herkunft ({nm.get(refs[0])!r})", nm.get(refs[0]) == f"Bert Gast ({FH})")
    ok(f"sonst eigener Chat-Kontakt ({nm.get(refs[1])!r})", nm.get(refs[1]) == f"Cora Chat ({FH})")
    ok(f"sonst zwischengespeicherter Anzeigename ({nm.get(refs[2])!r})", nm.get(refs[2]) == f"Dora Person ({FH})")
    ok(f"Chat-Kontakt einer anderen Person zaehlt nicht -> leer ({nm.get(refs[3])!r})", nm.get(refs[3]) == "")
    ok(f"Kennung als Name im Adressbuch zaehlt nicht -> Chat-Kontakt ({nm.get(refs[4])!r})", nm.get(refs[4]) == f"Eva Chat ({FH})")
    ok(f"leerer Name im Adressbuch, sonst nichts -> leer ({nm.get(refs[5])!r})", nm.get(refs[5]) == "")
    ok(f"unbekannter Server -> leer ({nm.get(refs[6])!r})", nm.get(refs[6]) == "")
    ok("lokale Ausnahme weiter mit Anzeigenamen, nie Anmeldename", nm.get(local_ref(B)) == "Bert Status" and B not in json.dumps(v))
    with connect() as c:
        ok("GET-Ansicht (view) gleich", {x["ref"]: x["name"] for x in US.view(c, A)["exceptions"]} == nm)
        ex = US.export(c, A)["exceptions"]
    ok("Backup-Export: nur Kennungen, keine Namen", all(set(x) == {"type", "ref"} for x in ex) and len(ex) == 8)
    with connect() as c, c:
        fed_clean(c)
    reset(A)


# --- Ruhezeiten --------------------------------------------------------------------------------------------------------
def part_quiet():
    reset(A)
    put(A, state="available")
    quiet(A, f="22:00", to="07:00", tz="America/New_York")
    for t, want, label in ((T0, "available", "19:00 New York"), (T0 + 3 * 3600 - 60, "available", "21:59"),
                           (T0 + 3 * 3600, "quiet", "22:00"), (T0 + 5 * 3600 + 1800, "quiet", "00:30 (nach Mitternacht)"),
                           (T0 + 12 * 3600 - 60, "quiet", "06:59"), (T0 + 12 * 3600, "available", "07:00")):
        clock(t)
        got = R(A, D, "chat")[3]
        ok(f"Ruhezeit 22-07 America/New_York um {label}: {got}", got == want)
    quiet(A, f="22:00", to="07:00", tz="Europe/Berlin")
    clock(T0)                                           # 01:00 Berlin
    ok("dieselbe Zeit in Europe/Berlin (01:00): Ruhezeit", R(A, D, "chat")[3] == "quiet")
    quiet(A, f="22:00", to="07:00", tz="")
    ok("ohne Zeitzone: Zeitzone des Servers (TIMEZONE, Standard Europe/Berlin)",
       R(A, D, "chat")[3] == ("quiet" if US.quiet_now({"from": 1320, "to": 420, "tz": ""}, T0) else "available"))
    quiet(A, tz="Europe/Berlin")
    with connect() as c:
        v = US.view(c, A)
    ok(f"Ansicht: quiet true, effective dnd, why quiet ({v['quiet']}, {v['effective']}, {v['why']})",
       v["quiet"] is True and v["effective"] == "dnd" and v["why"] == "quiet" and v["state"] == "available")
    prefs(A, quiet_dnd=False)
    ok("quiet_dnd aus: Ruhezeit wirkt nicht auf reachability", R(A, D, "chat")[3] == "available")
    prefs(A, quiet_dnd=True)
    quiet(A, on=False, tz="Europe/Berlin")
    ok("Ruhezeiten aus", R(A, D, "chat")[3] == "available")
    quiet(A, f="09:00", to="09:00", tz="Europe/Berlin")
    ok("von = bis: keine Ruhezeit", R(A, D, "chat")[3] == "available")
    reset(A)


# --- Cache -----------------------------------------------------------------------------------------------------------
class Count:
    """Zaehlt db.Conn.execute im with-Block."""

    def __enter__(self):
        self.n, self.orig, me = 0, db.Conn.execute, self

        def ex(conn, sql, args=()):
            me.n += 1
            return me.orig(conn, sql, args)
        db.Conn.execute = ex
        return self

    def __exit__(self, *a):
        db.Conn.execute = self.orig
        return False


def part_cache():
    reset(A)
    clock(T0)
    put(A, state="busy")
    R(A, D, "chat")
    with connect() as c:
        with Count() as n:
            US.reachability(c, A, D, "chat")
    ok(f"Cache-Treffer: eine Abfrage je reachability (chat, {n.n})", n.n == 1)
    US._CACHE.pop(A, None)
    with connect() as c:
        with Count() as n2:
            US.reachability(c, A, D, "chat")
    ok(f"Cache leer: zwei Abfragen ({n2.n})", n2.n == 2)
    prefs(A, busy="silent")
    ok("prefs geaendert (ver): sofort wirksam", R(A, D, "chat") == ("normal", True, False, "busy"))
    with connect() as c, c:                              # an der API vorbei: gleiche ver, aber updated neu
        c.execute("UPDATE user_status SET prefs='{}'::jsonb, updated=updated+1 WHERE uid=?", (A,))
    ok("gleiche ver, anderes updated: neu gelesen", R(A, D, "chat") == ("normal", True, True, "busy"))
    quiet(A, tz="Europe/Berlin")
    ok("Ruhezeit geaendert (ohne ver): sofort wirksam", R(A, D, "chat")[3] == "quiet")
    reset(A)
    ok("Zeile geloescht: wieder Standard", R(A, D, "chat") == ("normal", True, True, "available"))


# --- Kontoloeschung, Backup ---------------------------------------------------------------------------------------------
def part_account():
    reset(A)
    clock(T0)
    put(A, state="dnd")
    prefs(A, repeat_s=240, exceptions=[{"type": "user", "ref": local_ref(B)}, {"type": "user", "ref": local_ref(C)},
                                       {"type": "group", "name": GRP}])
    with connect() as c:
        exp = US.export(c, A)
    s = json.dumps(exp)
    ok(f"Backup-Export: Einstellungen und Ausnahmen als Handles ({s[:160]})", exp["prefs"] == {"repeat_s": 240} and
       {"type": "user", "ref": local_ref(B)} in exp["exceptions"] and {"type": "group", "name": GRP} in exp["exceptions"]
       and B not in s and C not in s)
    reset(D)
    put(D, state="busy")                                 # Pruefer: Zeile nur mit Zustand = noch keine Einstellungen
    with connect() as c, c:
        n = US.restore(c, D, exp)
    with connect() as c:
        v = US.view(c, D)
    ok(f"Einspielen in ein Konto ohne Status-Einstellungen ({n}; Gruppe nur, wenn Mitglied; Zustand bleibt {v['state']})",
       n == 1 and v["prefs"]["repeat_s"] == 240 and v["state"] == "busy" and
       [x["type"] for x in v["exceptions"]] == ["user", "user"], v["exceptions"])
    with connect() as c, c:
        n2 = US.restore(c, D, exp)
    ok("zweites Einspielen aendert nichts", n2 == 0)
    from saga.userdata import USER_TABLES, delete_user_data
    ok("user_status in USER_TABLES", "user_status" in USER_TABLES)
    put(B, state="busy")
    delete_user_data(B)
    with connect() as c:
        left = c.execute("SELECT COUNT(*) FROM user_status WHERE uid=?", (B,)).fetchone()[0]
        exc = c.execute("SELECT exc FROM user_status WHERE uid=?", (A,)).fetchone()[0]
    ok(f"Kontoloeschung: eigene Zeile weg, aus Ausnahmen anderer entfernt ({left}, {exc})",
       left == 0 and all(x.get("u") != B for x in exc) and any(x.get("u") == C for x in exc))
    auth.create_user(B, "Rs-Pass-12345", "Bert Neu")
    ok("neues gleichnamiges Konto erbt keine Ausnahme", R(A, B, "chat")[3] == "dnd")
    reset(A)
    reset(D)


# --- Perf --------------------------------------------------------------------------------------------------------------
def p95(xs):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))]


def part_perf():
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools", "perf", "limits.json"), encoding="utf-8") as f:
        lim = json.load(f)
    name = "Status: reachability"
    reset(A)
    clock(T0)
    # S: verfuegbar, Standard-Einstellungen, Anruf (mit "im Anruf"-Pruefung)
    with connect() as c:
        US.reachability(c, A, D, "call")
        xs = []
        for _ in range(400):
            t0 = time.perf_counter()
            US.reachability(c, A, D, "call")
            xs.append((time.perf_counter() - t0) * 1000)
    s = p95(xs)
    ok(f"Perf S: {name} p95 {s:.2f} ms (Median {statistics.median(xs):.2f}, Grenze {lim['S'].get(name)})", s <= lim["S"][name])
    # M: dnd, 100 Ausnahmen (99 Personen anderer Server + Gruppe), Anrufende ohne Ausnahme, 2.000 Anrufe in chat_call
    put(A, state="dnd")
    prefs(A, exceptions=[{"type": "user", "ref": f"~{'a' * 15}{chr(97 + i % 26)}@m{i}.example"} for i in range(99)] +
          [{"type": "group", "name": GRP}])
    with connect() as c, c:
        cid = chat_id()
        c.executemany("INSERT INTO chat_call (id, chat_id, caller, callee, caller_device, media, state, created, kind, hold) "
                      "VALUES (?,?,?,?,'d1','audio','ended',?,'dm',?)",
                      [(f"rs-p{i}", cid, B if i % 2 else D, A if i % 3 else C, T0 - 300 - i, "dnd" if i % 4 == 0 else None)
                       for i in range(2000)])
    with connect() as c:
        US.reachability(c, A, D, "call")
        xs = []
        for _ in range(400):
            t0 = time.perf_counter()
            r = US.reachability(c, A, D, "call")
            xs.append((time.perf_counter() - t0) * 1000)
    m = p95(xs)
    ok(f"Perf M: {name} p95 {m:.2f} ms (Median {statistics.median(xs):.2f}, Grenze {lim['M'].get(name)}, {r.why})",
       m <= lim["M"][name] and r.why == "dnd")
    reset(A)


def main():
    cleanup()
    for u, n in ((A, "Anna Status"), (B, "Bert Status"), (C, "Cora Status"), (D, "Dora Status")):
        auth.create_user(u, "Rs-Pass-12345", n)
    with connect() as c, c:
        auth.ensure_group(c, GRP)
        auth.ensure_group(c, "rs-fremd")
    for u in (A, C):
        auth.set_user_groups(auth.get_user(u)["id"], [GRP])
    auth.set_user_groups(auth.get_user(D)["id"], [])
    bus.set_sink(lambda kind, payload: EVENTS.append((kind, payload)))
    ok("Haken IN_CALL verdrahtet (calls._talking: nur angenommene Anrufe, 2029-2)", US.IN_CALL is calls._talking)
    try:
        for part in (part_matrix, part_rank_and_misc, part_expiry, part_repeat, part_exceptions, part_fed_names, part_quiet, part_cache,
                     part_account, part_perf):
            try:
                part()
            except Exception as ex:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                ok(f"{part.__name__}: Ausnahme {type(ex).__name__}: {ex}", False)
    finally:
        bus.set_sink(None)
        testclock.set_clock({"reset": True})
        cleanup()
    print(f"\nREACHABILITY {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
