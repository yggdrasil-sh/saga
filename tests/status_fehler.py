"""Eigener Status ueber die API (2.6, Bugs-DB 2029-1, saga/user_status.py): /api/account/status und .../prefs gegen den
laufenden Server - Fehlerfaelle, Vertrag, Live-Ereignis, RT-29.

Prueft: ohne Anmeldung / ohne X-Saga 401 · Standard-Ansicht (Felder, Standards, Grenzen) · PUT-Fehler (Zustand, Ende in
der Vergangenheit / ueber ein Jahr, until + dur, dur zu kurz, Text zu lang/kein Text, Zusatzfeld, source system, ver
falsch, kein JSON, Liste) -> 400 ohne Echo der Eingabe · ver veraltet -> 409 mit aktuellem Stand, zwei gleichzeitig mit
gleicher ver -> genau einer · prefs-Fehler (Schluessel, Werte, repeat_s-Grenzen, Ausnahmen > 100, contact/e164
reserviert, unbekannte Person, fremde Gruppe) · Fokus beendet keinen selbst gesetzten Status · RT-29: Fokus-Name
"Therapie" nirgends in Antwort, Datenbank (auch keine Spalte dafuer) und Server-Log · Steuerzeichen im Text · Live
status.changed nur an die eigenen Verbindungen · Ablauf-Schleife (until <= jetzt sofort available, Schleife setzt
binnen 35 s zurueck und meldet) · /api/version userstatus1 (status1 bleibt der Datenbank-Status), /api/chat/me status1 ·
GET /api/status bleibt der Dienst-Status des Dashboards · keine Serverfehler.
2029-3: Sammelmeldung status.digest live nur an die eigenen Verbindungen, ohne Namen/Text, genau einmal, nicht bei Ruhezeit.
Wegwerf-Konten sf-*, Gruppe sf-team."""
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth  # noqa: E402
from db import connect  # noqa: E402
import chat as CT  # noqa: E402  (tests/chat.py: req, ck, Stream)
from saga.core import local_ref  # noqa: E402

req, ck, res = CT.req, CT.ck, CT.res


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, "" if c else str(info)[:300], flush=True)


PW = "Status-Pass-1234"
A, B, C = "sf-anna", "sf-bert", "sf-cora"
FOCUS = "Therapie"
LOG = os.environ.get("SAGA_SERVER_LOG", "")


def errs():
    try:
        with open(LOG, encoding="utf-8", errors="replace") as f:
            return f.read().count("Traceback")
    except OSError:
        return 0


def log_has(s):
    try:
        with open(LOG, encoding="utf-8", errors="replace") as f:
            return s in f.read()
    except OSError:
        return None


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'sf-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM user_status WHERE uid LIKE 'sf-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'sf-%'")
        c.execute("DELETE FROM groups WHERE name LIKE 'sf-%'")


def put(k, body, path="/api/account/status"):
    return req("PUT", path, k, body)


def run():
    ka, kb = ck(A), ck(B)
    # --- Anmeldung ------------------------------------------------------------------------------------------------------
    s, _ = req("GET", "/api/account/status")
    ok(f"ohne Anmeldung: 401 ({s})", s == 401)
    s, _ = req("GET", "/api/account/status", hdr={"Cookie": "saga=" + ka})
    ok(f"Sitzung ohne X-Saga: 401 ({s})", s == 401)
    s, _ = req("PUT", "/api/account/status/prefs", None, {"repeat": False})
    ok(f"prefs ohne Anmeldung: 401 ({s})", s == 401)

    # --- Standard-Ansicht ------------------------------------------------------------------------------------------------
    s, v = req("GET", "/api/account/status", ka)
    keys = {"state", "until", "text", "source", "since", "updated", "ver", "effective", "why", "quiet", "in_call", "prefs",
            "exceptions", "limits", "now"}
    ok(f"GET: Vertrag (Felder {sorted(set(v) ^ keys) if isinstance(v, dict) else v})", s == 200 and set(v) == keys)
    ok("GET: Standard available, ver 0, keine Ausnahmen", v["state"] == "available" and v["effective"] == "available"
       and v["ver"] == 0 and v["exceptions"] == [] and v["until"] is None and v["text"] is None)
    ok(f"GET: Standards ({v['prefs']})", v["prefs"] == {"quiet_dnd": True, "quiet_digest": False, "in_call": "knock",
                                                      "show_call": True, "busy": "show", "repeat": True, "repeat_s": 180,
                                                      "mention_break": False})
    ok(f"GET: Grenzen ({v['limits']})", v["limits"]["text"] == 80 and v["limits"]["exceptions"] == 100 and
       v["limits"]["repeat_s"] == [30, 3600])
    ok("GET: kein Cache", CT.req.last_headers.get("cache-control") == "no-store")

    # --- PUT-Fehler ------------------------------------------------------------------------------------------------------
    now = time.time()
    SECRET = "Geheimwert-xyz"
    bads = [({}, "ohne state"), ({"state": "away"}, "unbekannter Zustand"), ({"state": SECRET}, "Zustand als Text"),
            ({"state": "dnd", "until": now - 10}, "Ende in der Vergangenheit"),
            ({"state": "dnd", "until": now + 367 * 86400}, "Ende ueber ein Jahr"),
            ({"state": "dnd", "until": now + 600, "dur": 600}, "until und dur"), ({"state": "dnd", "dur": 30}, "dur unter 60"),
            ({"state": "dnd", "until": "morgen"}, "until als Text"), ({"state": "dnd", "until": True}, "until als bool"),
            ({"state": "busy", "text": "x" * 81}, "Text 81 Zeichen"), ({"state": "busy", "text": ["x"]}, "Text als Liste"),
            ({"state": "busy", "focus": SECRET}, "Zusatzfeld"), ({"state": "busy", "source": "system"}, "source system"),
            ({"state": "busy", "source": SECRET}, "source unbekannt"), ({"state": "busy", "ver": "1"}, "ver als Text"),
            ({"state": "busy", "ver": True}, "ver als bool")]
    for body, what in bads:
        s, d = put(ka, body)
        ok(f"PUT {what}: 400 ohne Echo ({s})", s == 400 and SECRET not in json.dumps(d) and "error" in d)
    s, d = req("PUT", "/api/account/status", ka, raw=b"kein json")
    ok(f"PUT kein JSON: 400 ({s})", s == 400)
    s, d = req("PUT", "/api/account/status", ka, raw=b"[1]", hdr={"Content-Type": "application/json"})
    ok(f"PUT Liste: 400 ({s})", s == 400)
    s, d = req("GET", "/api/account/status", ka)
    ok("nach allen Fehlern: nichts gespeichert (ver 0)", d["ver"] == 0)

    # --- Gueltig, ver, 409 -----------------------------------------------------------------------------------------------
    s, v = put(ka, {"state": "busy", "text": "x" * 80})
    ok(f"PUT busy mit 80 Zeichen: 200, ver 1 ({s}, {v.get('ver')})", s == 200 and v["ver"] == 1 and v["text"] == "x" * 80
       and v["source"] == "manual")
    s, v = put(ka, {"state": "busy", "text": "  Zeile\u0001eins\n zwei  "})
    ok(f"Steuerzeichen im Text -> Leerzeichen, getrimmt ({v.get('text')!r})", s == 200 and v["text"] == "Zeile eins  zwei")
    since = v["since"]
    s, v = put(ka, {"state": "busy", "text": "neu", "ver": v["ver"]})
    ok("gleicher Zustand: since bleibt, ver steigt", s == 200 and v["since"] == since and v["ver"] == 3)
    s, d = put(ka, {"state": "dnd", "ver": 1})
    ok(f"veraltete ver: 409 mit aktuellem Stand ({s}, ver {d.get('ver')})", s == 409 and d["ver"] == 3 and d["state"] == "busy"
       and "error" in d)
    out = []
    ths = [threading.Thread(target=lambda: out.append(put(ka, {"state": "dnd", "ver": 3})[0])) for _ in range(2)]
    [x.start() for x in ths]
    [x.join() for x in ths]
    ok(f"zwei gleichzeitig mit gleicher ver: genau einer ({sorted(out)})", sorted(out) == [200, 409])
    s, v = put(ka, {"state": "dnd", "dur": 3600})
    ok(f"dur: Ende = jetzt + dur ({round(v['until'] - v['now'])})", s == 200 and abs(v["until"] - v["now"] - 3600) < 2)
    s, v = put(ka, {"state": "available", "until": time.time() + 600, "text": "Da"})
    ok("available: ohne Ende, Text bleibt", s == 200 and v["until"] is None and v["text"] == "Da")

    # --- prefs-Fehler ------------------------------------------------------------------------------------------------------
    P = "/api/account/status/prefs"
    pb = [({}, "leer"), ({"x": 1}, "unbekannter Schluessel"), ({"in_call": "ring"}, "in_call unbekannt"),
          ({"busy": "laut"}, "busy unbekannt"), ({"repeat_s": 29}, "repeat_s 29"), ({"repeat_s": 3601}, "repeat_s 3601"),
          ({"repeat_s": True}, "repeat_s bool"), ({"repeat_s": 180.5}, "repeat_s Bruch"), ({"repeat": "ja"}, "repeat Text"),
          ({"show_call": 1}, "show_call Zahl"), ({"exceptions": "alle"}, "exceptions Text"),
          ({"exceptions": [{"type": "group", "name": "sf-team"}] * 101}, "101 Ausnahmen"),
          ({"exceptions": [{"type": "contact", "ref": SECRET}]}, "contact reserviert"),
          ({"exceptions": [{"type": "e164", "ref": "+4930123456"}]}, "e164 reserviert"),
          ({"exceptions": [{"type": "user", "ref": "~aaaaaaaaaaaaaaaa"}]}, "unbekannte Person"),
          ({"exceptions": [{"type": "user", "ref": SECRET}]}, "unbekannter Name"),
          ({"exceptions": [{"type": "group", "name": "sf-fremd"}]}, "fremde Gruppe"),
          ({"exceptions": [{"type": "user"}]}, "ohne ref")]
    for body, what in pb:
        s, d = put(ka, body, P)
        ok(f"prefs {what}: 400 ohne Echo ({s})", s == 400 and SECRET not in json.dumps(d))
    s, d = put(ka, {"exceptions": [{"type": "contact", "ref": "x"}]}, P)
    ok(f"reserviert: eigene Meldung ({d.get('error')})", "reserviert" in str(d.get("error")))
    s, v = put(ka, {"repeat_s": 240, "exceptions": [{"type": "user", "ref": local_ref(B)}, {"type": "group", "name": "sf-team"}]}, P)
    ok(f"prefs gueltig: Teil-Aenderung, Ausnahmen mit Handle und Name ({v.get('exceptions')})", s == 200 and
       v["prefs"]["repeat_s"] == 240 and v["prefs"]["repeat"] is True and v["exceptions"] == [
           {"type": "user", "ref": local_ref(B), "name": "Bert Status"}, {"type": "group", "name": "sf-team"}])
    ok("Antwort ohne Anmeldenamen anderer", B not in json.dumps(v))

    # --- Fokus und RT-29 -----------------------------------------------------------------------------------------------------
    s, v = put(ka, {"state": "dnd", "text": "Bin weg"})
    s, v = put(ka, {"state": "available", "source": "focus"})
    ok(f"Fokus-Ende hebt selbst gesetztes Nicht stoeren nicht auf (kept {v.get('kept')})", s == 200 and v["state"] == "dnd"
       and v.get("kept") is True and v["source"] == "manual")
    s, v = put(ka, {"state": "dnd", "source": "focus", "text": FOCUS})
    ok("RT-29: Fokus mit Name -> 200, kein Text gespeichert", s == 200 and v["text"] is None and v["source"] == "focus")
    body_all = json.dumps(v)
    s, v2 = req("GET", "/api/account/status", ka)
    s3, d3 = put(ka, {"state": "dnd", "source": "focus", "focus": FOCUS})
    with connect() as c:
        row = c.execute("SELECT row_to_json(s)::text FROM user_status s WHERE uid=?", (A,)).fetchone()[0]
        cols = sorted(r[0] for r in c.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() "
                                              "AND table_name='user_status'"))
        anywhere = c.execute("SELECT COUNT(*) FROM user_kv WHERE v LIKE ?", (f"%{FOCUS}%",)).fetchone()[0]
    ok(f"RT-29: '{FOCUS}' nicht in Antworten (PUT, GET, 400)", FOCUS not in body_all and FOCUS not in json.dumps(v2) and
       s3 == 400 and FOCUS not in json.dumps(d3))
    ok("RT-29: nicht in der Datenbank (Zeile, user_kv)", FOCUS not in row and anywhere == 0, row)
    ok(f"RT-29: keine Spalte fuer Fokus-Namen ({cols})", cols == ["exc", "prefs", "since", "source", "state", "text", "uid",
                                                                   "until", "updated", "ver"])
    lh = log_has(FOCUS)
    ok(f"RT-29: nicht im Server-Log ({'kein Log' if lh is None else lh})", lh is False or (lh is None and not LOG))
    s, v = put(ka, {"state": "busy", "source": "focus"})
    ok("Fokus darf Fokus ersetzen", s == 200 and v["state"] == "busy" and v["source"] == "focus")
    s, v = put(ka, {"state": "available", "source": "focus"})
    ok("Fokus-Ende nach Fokus: available", s == 200 and v["state"] == "available" and not v.get("kept"))

    # --- Live, Ablauf ----------------------------------------------------------------------------------------------------------
    sa, sb = CT.Stream(ka), CT.Stream(kb)
    time.sleep(0.8)
    ma, mb = sa.mark(), sb.mark()
    s, v = put(ka, {"state": "busy", "text": "Live"})
    e = sa.wait("status.changed", lambda d: d.get("ver") == v["ver"], since=ma)
    ok(f"Live: status.changed an die eigene Verbindung (ver {e and e.get('ver')})", e is not None and e["state"] == "busy"
       and e["text"] == "Live")
    time.sleep(0.5)
    ok("Live: andere Konten bekommen nichts", "status.changed" not in sb.types(mb))
    with connect() as c, c:
        c.execute("UPDATE user_status SET state='dnd', until=? WHERE uid=?", (time.time() - 1, A))
    s, v = req("GET", "/api/account/status", ka)
    ok("until <= jetzt: sofort available (vor der Schleife)", v["state"] == "available" and v["effective"] == "available")
    ma = sa.mark()
    e = sa.wait("status.changed", lambda d: d.get("state") == "available" and d.get("source") == "system", since=ma, timeout=40)
    with connect() as c:
        row = c.execute("SELECT state, until, text, source FROM user_status WHERE uid=?", (A,)).fetchone()
    ok(f"Ablauf-Schleife (30 s): zurueckgesetzt und gemeldet ({row})", e is not None and tuple(row) == ("available", None, None, "system"))

    # --- Faehigkeiten, bestehende Endpunkte ----------------------------------------------------------------------------------
    s, d = req("GET", "/api/version")
    ok("/api/version: userstatus1, status1 (Datenbank-Status) bleibt", "userstatus1" in d.get("features", []) and
       "status1" in d.get("features", []))
    s, d = req("GET", "/api/chat/me", ka)
    ok(f"/api/chat/me: features userstatus1 ({s})", s == 200 and "userstatus1" in (d.get("features") or []))
    s, d = req("GET", "/api/status", ka)
    ok(f"GET /api/status bleibt der Dienst-Status des Dashboards ({s})", s == 200 and isinstance(d, dict) and "effective" not in d)

    # --- 2029-3: Sammelmeldung live (status.digest nur an die eigenen Verbindungen, ohne Namen, genau einmal) ------------
    from saga import bus, push  # noqa: F401 - push haengt sich an push.send und status.dnd_end
    s, v = put(ka, {"state": "dnd", "text": "Konzentration"})
    with connect() as c, c:
        for _ in range(2):                  # C: keine Ausnahme (B und die Gruppe sf-team sind oben Ausnahmen)
            bus.fire("push.send", c, uid=A, kind="chat.message", actor=C, data={"chat": "sf-1"})
        bus.fire("push.send", c, uid=A, kind="chat.mention", actor=C, data={"chat": "sf-1"})
    ma, mb = sa.mark(), sb.mark()
    s, v = put(ka, {"state": "available"})
    e = sa.wait("status.digest", since=ma)
    ok(f"Live: status.digest an die eigene Verbindung ({e})", s == 200 and e is not None and e.get("messages") == 3
       and e.get("calls") == 0 and e.get("other") == 0 and e.get("since") and e.get("until") >= e.get("since"))
    ok("Live: Sammelmeldung ohne Namen und ohne Text", e is not None and C not in json.dumps(e) and "Cora" not in json.dumps(e)
       and "Konzentration" not in json.dumps(e))
    time.sleep(0.5)
    ok("Live: andere Konten bekommen keine Sammelmeldung", "status.digest" not in sb.types(mb))
    ma = sa.mark()
    put(ka, {"state": "dnd"})
    put(ka, {"state": "available"})
    time.sleep(1.0)
    ok("Nicht stoeren ohne Zurueckgehaltenes: keine Sammelmeldung, auch keine zweite", "status.digest" not in sa.types(ma))
    fr, to = time.strftime("%H:%M", time.gmtime(time.time() - 3600)), time.strftime("%H:%M", time.gmtime(time.time() + 3600))
    s, _ = req("PUT", "/api/push/settings", ka, {"quiet": {"on": True, "from": fr, "to": to, "tz": "UTC"}})
    s2, v = req("GET", "/api/account/status", ka)
    with connect() as c, c:
        n = push.enqueue(c, A, "chat.message", actor=C, data={"chat": "sf-2"})
        hk = c.execute("SELECT COUNT(*) FROM user_kv WHERE uid=? AND k='status_held'", (A,)).fetchone()[0]
    ok(f"Ruhezeit (why quiet): keine Mitteilung, nichts fuer eine Sammelmeldung gezaehlt ({s}, {v.get('why')}, {n}, {hk})",
       s == 200 and v["why"] == "quiet" and n == 0 and hk == 0)
    req("PUT", "/api/push/settings", ka, {"quiet": {"on": False}})


def main():
    cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled",)}
    auth.save_setting("chat_enabled", True)
    for u, n in ((A, "Anna Status"), (B, "Bert Status"), (C, "Cora Status")):
        auth.create_user(u, PW, n)
    with connect() as c, c:
        auth.ensure_group(c, "sf-team")
        auth.ensure_group(c, "sf-fremd")
    for u in (A, B):
        auth.set_user_groups(auth.get_user(u)["id"], ["sf-team"])
    e0 = errs()
    try:
        run()
    except Exception as ex:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        ok(f"Ausnahme {type(ex).__name__}: {ex}", False)
    finally:
        for s in CT.STREAMS:
            s.close()
        for k, v in keep.items():
            auth.save_setting(k, v)
        cleanup()
    n = errs() - e0
    ok(f"Serverfehler: {n}", n == 0)
    print(f"\nSTATUS-FEHLER {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
