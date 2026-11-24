"""Faehigkeiten (Bugs-DB 2261, Konzept 2.6 K18): can(uid, cap) als ein Rechtemechanismus - Matrix Faehigkeit x Gruppe,
Voraussetzung "ai", reservierte tel*-Namen immer aus, leere Gruppenliste = alle (bestehende Server aendern sich nicht),
/api/account liefert caps, eingeschraenkte Gruppe (ohne ai_act) bekommt keine Websuche (web_step ohne Modellaufruf),
Admin-Formular speichert die Gruppen je Faehigkeit. Braucht nur die Datenbank; Wegwerf-Konten fk-*, stellt die
KI-Einstellung wieder her.
Aufruf: PYTHONPATH=tests/pgshim:. python tests/faehigkeiten.py"""
import sys

import auth
from db import connect
from saga import ai, caps

res = []


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, "" if c else info, flush=True)


USERS = {"fk-eltern": ["fk-familie", "fk-eltern"], "fk-kind": ["fk-familie", "fk-kinder"], "fk-gast": []}


def main():
    prev = dict(auth.settings(fresh=True).get("ai") or {})
    try:
        for u, gs in USERS.items():
            if not auth.get_user(u):
                auth.create_user(u, "Fk-Pass-12345", u)
            auth.set_user_groups(auth.get_user(u)["id"], gs)
        base = dict(prev, enabled=True, provider="openai", url="http://127.0.0.1:18290/v1", model="fake")

        def setc(**kw):
            auth.save_setting("ai", dict(base, **kw))
            auth.settings(fresh=True)

        # --- Standard: keine Gruppen -> alle duerfen alles (ausser tel*) --------------------------------------------
        setc(groups=[], caps={})
        ok("Standard: alle Faehigkeiten fuer alle", all(caps.can(u, x) for u in USERS for x in caps.CAPS))
        ok("reserviert fuer 3.0: tel* immer aus", not any(caps.can("fk-eltern", x) for x in caps.RESERVED))
        ok("unbekannte Faehigkeit: aus", not caps.can("fk-eltern", "admin") and not caps.can("", "ai"))
        ok("caps_of in fester Reihenfolge", caps.caps_of("fk-kind") == list(caps.CAPS), caps.caps_of("fk-kind"))

        # --- Assistent nur fuer die Familie; Auftraege nach aussen nur fuer Eltern --------------------------------
        setc(groups=["fk-familie"], caps={"ai_act": ["fk-eltern"], "ai_ideas": []})
        m = {u: caps.caps_of(u) for u in USERS}
        ok("Eltern: ai, ai_act, ai_ideas", m["fk-eltern"] == ["ai", "ai_act", "ai_ideas"], m)
        ok("Kind (eingeschraenkt): ai, ai_ideas - ohne ai_act", m["fk-kind"] == ["ai", "ai_ideas"], m)
        ok("Gast ohne Familie: nichts (ai ist Voraussetzung)", m["fk-gast"] == [], m)
        with connect() as c:
            ok("can mit Verbindung = ohne", caps.can("fk-kind", "ai_act", c) is False and caps.can("fk-eltern", "ai_act", c) is True)
        setc(groups=["fk-familie"], caps={"ai_act": ["FK-Eltern"], "ai_ideas": ["fk-niemand"]})
        ok("Gruppen gross/klein egal; ai_ideas nur fuer die genannte Gruppe",
           caps.can("fk-eltern", "ai_act") and not caps.can("fk-eltern", "ai_ideas"))

        # --- Eingeschraenkt: keine Websuche (vor jedem Modellaufruf) -----------------------------------------------
        setc(groups=["fk-familie"], caps={"ai_act": ["fk-eltern"]}, web=True)
        calls = []
        orig = ai.stream_model
        ai.stream_model = lambda *a, **k: calls.append(1) or {}
        try:
            out = ai.web_step("fk-kind", ai.cfg(), "Wie wird das Wetter morgen?", lambda *a: None, lambda: False)
        finally:
            ai.stream_model = orig
        ok("Kind: web_step sucht nicht und fragt das Modell nicht", out["sources"] == [] and not calls, (out, calls))

        # --- Konto-Antwort und Admin-Formular ----------------------------------------------------------------------
        from saga import web_auth
        ok("/api/account: caps fuer die Person", web_auth._caps_of("fk-kind") == ["ai"]
           or web_auth._caps_of("fk-kind") == ["ai", "ai_ideas"], web_auth._caps_of("fk-kind"))
        src = open(web_auth.__file__, encoding="utf-8").read()
        ok("/api/account liefert das Feld caps", '"caps": _caps_of(uid)' in src)
        import admin
        v, err = admin._ai_from({"provider": "openai", "caps_ai_act": "fk-eltern, FK-Eltern\nfk-oma", "caps_ai_ideas": ""}, {})
        ok("Admin: Gruppen je Faehigkeit (bereinigt, ohne Doppelte)", not err and v["caps"] == {"ai_act": ["fk-eltern", "fk-oma"], "ai_ideas": []},
           v.get("caps"))
    finally:
        auth.save_setting("ai", prev)
        for u in USERS:
            x = auth.get_user(u)
            if x:
                with connect() as c, c:
                    c.execute("DELETE FROM user_group WHERE user_id=?", (x["id"],))
                    c.execute("DELETE FROM users WHERE id=?", (x["id"],))
        with connect() as c, c:
            c.execute("DELETE FROM groups WHERE name LIKE 'fk-%' AND id NOT IN (SELECT group_id FROM user_group)")
    print(f"\nFaehigkeiten {sum(res)}/{len(res)}" + (" - ALLES GRUEN" if all(res) else " - FEHLER"))
    return 0 if all(res) else 1


if __name__ == "__main__":
    sys.exit(main())
