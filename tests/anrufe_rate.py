"""Bugs-DB 2356: Anruf-Grenzen (CALL_RATE, CALL_RATE_H, PAIR_RATE) je Anrufversuch genau einmal - Logiktest ohne laufende
Instanz (Datenbank, Gegenstelle und Knoten nachgebildet, saga/fed_calls.py und saga/calls.py rate_call/rate_back echt).

Befund: im foederierten Direkt-Chat mit einer Gegenstelle, die auf Knoten besteht (horn_req), zog dm_horn_lead_start ab,
bekam 409 horn_block node/off, fiel auf dm_horn_start zurueck (zog erneut ab), und der direkte Anruf nach 409 fallback
(FED.start) zog ein drittes Mal ab - der zweite Anruf in einer Minute an dieselbe Person endete in 429.
Geprueft: ein Klick (POST /api/calls via horn -> 409 fallback -> POST /api/calls direkt, Web mit neuer id, Apps mit
derselben) = eine Abbuchung, in allen Wegen (Gegenstelle fuehrt -> node -> wir fuehren -> off; Gegenstelle fuehrt -> peer;
wir fuehren -> off); 3 Anrufe je Minute erlaubt, der 4. 429 (ohne Auftrag an die Gegenstelle); Rueckfall-Guthaben nur
einmal und nur fuer den direkten Anruf (Versuche ueber Knoten zahlen immer); vor der Abbuchung abgelehnte Versuche
(besetzt, eingehender Anruf) zaehlen nicht; Erfolg ueber Knoten hinterlaesst kein Guthaben; 429 einer Grenze zaehlt in
den anderen nicht mit; dm_horn_lead_start/dm_horn_start: "{name} ruft dich gerade an" wie lokal.

Aufruf: PYTHONPATH=tests/pgshim:. python tests/anrufe_rate.py   (Umgebung env.sh, die Datenbank wird nicht benutzt)"""
import contextlib
import json
import os
import secrets
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from saga import calls as CL, fed_calls as X  # noqa: E402

RES = []
ME, PEER = "anna", "bert@b.example"
CH = {"id": 1, "ref": "r1"}
CAPS = json.dumps(["horn", "multi", "ws"])
ST = {"busy": False, "inc": None, "req": True, "dm": True}
ANS = {}
SENT = []


def ok(name, cond):
    RES.append(bool(cond))
    print(("OK   " if cond else "FAIL ") + name, flush=True)


class _C:
    def __init__(self):
        self.q = ""

    def execute(self, q, *a, **k):
        self.q = q
        return self

    def fetchone(self):
        if "caller=? AND callee=?" in self.q and ST["inc"]:
            return (ST["inc"],)
        return None

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@contextlib.contextmanager
def _db():
    yield _C()


def sync_call(p, body, path=None):
    SENT.append(body.get("op"))
    return ANS.get(body.get("op"), (409, {"horn_block": "off"}))


def setup(**kw):
    """Grenzen leeren, Gegenstelle/Antworten nachbilden. kw: Antworten je Auftrag (ring, dmhorn, dmhorn_lead) und ST."""
    CL._hits.clear()
    CL._BACK.clear()
    SENT.clear()
    ANS.clear()
    ANS.update(ring=(200, {"ok": True}), dmhorn=(409, {"horn_block": "off"}), dmhorn_lead=(409, {"horn_block": "node"}))
    ST.update(busy=False, inc=None, req=True, dm=True)
    for k, v in kw.items():
        (ST if k in ST else ANS)[k] = v


def stubs():
    X._db = _db
    X.chat._member = lambda c, chid, uid: {"state": "ok"}
    X.chat.members = lambda c, chid: [ME, PEER]
    X.sharing.is_remote = lambda u: "@" in u
    X.FC._ro_error = lambda c, ch: None
    X.FC.handle_of = lambda u: u
    X.FC.split_handle = lambda h: tuple(h.split("@", 1))
    X.FC.my_handle = lambda c, uid: ME + "@a.example"
    X.FC.label_of = lambda c, u: {"name": "Bert"}
    X.FC.sync_call = sync_call
    X.FC._volatile = lambda *a, **k: None
    X.F.peer_by = lambda c, k, v: {"id": 7, "host": v}
    X.peer_ok = lambda p: True
    X.peer3 = lambda p: False
    X.peer_horn_dm = lambda p: True
    X.peer_horn_req = lambda p: ST["req"]
    X.FH.fed_ok = lambda c, ch, *a: (True, None)
    X._horn_hint = lambda caps: {}
    X._apply_legs = lambda *a, **k: None
    X._horn_block = lambda *a, **k: ({}, None)
    CL.horn.any_ready = lambda c, ws=None: True
    CL.horn_dm_on = lambda: ST["dm"]
    CL._busy = lambda c, uid: ST["busy"]
    CL._dev_name = lambda c, dev: "Testgeraet"
    CL._leg_put = lambda *a, **k: None
    CL._load = lambda c, cid, *a: {"id": cid}
    CL._dict = lambda c, call, uid: {"id": call["id"]}
    CL.group_create = lambda c, ch, cid, *a, **k: {"id": cid}
    CL.horn_block = lambda *a, **k: {}
    CL.emit_roster = lambda *a, **k: None


def _out(r):
    return r.status_code, json.loads(bytes(r.body).decode())


def horn(cid=None):
    """POST /api/calls {via: horn} (dm_horn_route) -> (status, daten)"""
    return _out(X.dm_horn_route(ME, CH, cid or secrets.token_hex(16), "audio", "d" * 32, CAPS))


def direct(cid=None):
    """POST /api/calls direkt (FED.start) -> (status, daten)"""
    return _out(X.start(ME, CH, cid or secrets.token_hex(16), "audio", "d" * 32, {"e" * 32: "x"}, CAPS))


def click(same_id=False):
    """Ein Klick wie im Client: ueber Knoten, bei 409 fallback direkt (Web neue id, Apps dieselbe) -> Liste der Status."""
    cid = secrets.token_hex(16)
    s, d = horn(cid)
    if s == 409 and d.get("fallback") == "direct":
        s2, _ = direct(cid if same_id else None)
        return [s, s2]
    return [s]


def pair_n():
    return len(CL._hits.get(("pair", ME + "|" + PEER), []))


def main():
    stubs()
    # --- der Befund: Gegenstelle fuehrt -> node -> wir fuehren -> off -> direkt ------------------------------------
    for same, what in ((False, "Web, neue id"), (True, "Apps, dieselbe id")):
        setup()
        codes = [click(same) for _ in range(4)]
        ok(f"Gegenstelle fuehrt -> node -> wir -> off -> direkt ({what}): 3 Klicks erlaubt, der 4. 429 ({codes})",
           codes[:3] == [[409, 200]] * 3 and codes[3] == [429])
        ok(f"... je Klick genau eine Abbuchung ({pair_n()} im Topf pair, {len(CL._hits.get(('call', ME), []))} im Topf call)",
           pair_n() == 3 and len(CL._hits.get(("call", ME), [])) == 3 and len(CL._hits.get(("call-h", ME), [])) == 3)
        ok(f"... der 4. Klick schickt keinen Auftrag an die Gegenstelle ({SENT[-3:]})",
           SENT.count("dmhorn_lead") == 3 and SENT.count("dmhorn") == 3 and SENT.count("ring") == 3)

    # --- Gegenstelle fuehrt -> peer -> direkt (ohne zweiten Weg) -----------------------------------------------------
    setup(dmhorn_lead=(409, {"horn_block": "peer"}))
    codes = [click() for _ in range(4)]
    ok(f"Gegenstelle fuehrt -> peer -> direkt: 3 erlaubt, der 4. 429 ({codes}, {SENT.count('dmhorn')} dmhorn)",
       codes[:3] == [[409, 200]] * 3 and codes[3] == [429] and "dmhorn" not in SENT)

    # --- wir fuehren (Gegenstelle ohne horn_req) -> off -> direkt ----------------------------------------------------
    setup(req=False)
    codes = [click() for _ in range(4)]
    ok(f"wir fuehren -> off -> direkt: 3 erlaubt, der 4. 429 ({codes})", codes[:3] == [[409, 200]] * 3 and codes[3] == [429])

    # --- Gegenstelle fuehrt -> node, hier horn_all aus: fallback node -> direkt ---------------------------------------
    setup(dm=False)
    codes = [click() for _ in range(4)]
    ok(f"Gegenstelle fuehrt -> node, hier aus -> direkt: 3 erlaubt, der 4. 429 ({codes})",
       codes[:3] == [[409, 200]] * 3 and codes[3] == [429] and "dmhorn" not in SENT)

    # --- Guthaben nur einmal, nur fuer den direkten Anruf ------------------------------------------------------------
    setup()
    s, d = horn()
    a = direct()[0]
    n1 = pair_n()
    b = direct()[0]
    ok(f"Rueckfall-Guthaben einmal: direkt frei, der naechste direkte Anruf zahlt ({s}/{d.get('horn_block')}, {a}, {b}, "
       f"Topf {n1} -> {pair_n()})", s == 409 and d.get("fallback") == "direct" and a == b == 200 and n1 == 1 and pair_n() == 2)
    setup()
    r = [horn()[0] for _ in range(3)] + [horn()[0]]
    ok(f"Versuche ueber Knoten zahlen immer (kein Guthaben-Kreislauf): {r}", r == [409, 409, 409, 429])
    s = direct()[0]
    ok(f"... danach einmal direkt aus dem Guthaben des letzten Versuchs ({s})", s == 200 and pair_n() == 3)
    setup()
    horn()
    horn()
    ok(f"... Guthaben sammelt sich nicht an (zwei Rueckfaelle, dann zwei direkte: {[direct()[0], direct()[0]]}, Topf {pair_n()})",
       pair_n() == 3)

    # --- Pruefer: Guthaben verfaellt nach 60 s; Zeitablauf (502) hinterlaesst keines ---------------------------------
    setup()
    horn()
    CL._BACK[ME + "|" + PEER] -= 61
    a = direct()[0]
    ok(f"Pruefer: Rueckfall-Guthaben aelter als 60 s verfaellt, der direkte Anruf zahlt ({a}, Topf {pair_n()}, {len(CL._BACK)})",
       a == 200 and pair_n() == 2 and not CL._BACK)
    setup(dmhorn=(None, None))
    s, _ = horn()
    a = direct()[0]
    ok(f"Pruefer: Zeitablauf beim Gegenueber (502, dort kann es klingeln): kein Guthaben, direkt zahlt ({s}, {a}, Topf {pair_n()})",
       s == 502 and a == 200 and pair_n() == 2)

    # --- vor der Abbuchung abgelehnt: zaehlt nicht -------------------------------------------------------------------
    setup(busy=True)
    s, d = horn()
    ok(f"besetzt (Du telefonierst gerade schon): 409 ohne Abbuchung, ohne Auftrag ({s}, {pair_n()}, {SENT})",
       s == 409 and d.get("error") and "fallback" not in d and "incoming" not in d and pair_n() == 0 and SENT == [])
    ST["busy"] = False
    codes = [click() for _ in range(3)]
    ok(f"... danach weiter 3 Klicks erlaubt ({codes})", codes == [[409, 200]] * 3)

    # --- "ruft dich gerade an" auch ueber Knoten (wie lokal) ---------------------------------------------------------
    for req, what in ((True, "Gegenstelle fuehrt"), (False, "wir fuehren")):
        setup(req=req, inc="f" * 32)
        s, d = horn()
        ok(f"{what}: Rueckruf, waehrend der Anruf des Gegenuebers klingelt -> 409 'ruft dich gerade an' + incoming, "
           f"keine Abbuchung, kein Auftrag ({s}, {d.get('incoming')}, {pair_n()}, {SENT})",
           s == 409 and d.get("incoming") == "f" * 32 and "Bert" in (d.get("error") or "")
           and "fallback" not in d and pair_n() == 0 and SENT == [])

    # --- Erfolg ueber Knoten hinterlaesst kein Guthaben ----------------------------------------------------------------
    setup(dmhorn_lead=(200, {"ok": True, "legs": []}))
    s, _ = horn()
    a = direct()[0]
    ok(f"Gegenstelle fuehrt, Erfolg: der naechste direkte Anruf zahlt selbst ({s}, {a}, Topf {pair_n()})", s == a == 200 and pair_n() == 2)
    setup(dmhorn=(200, {"ok": True}))
    s, _ = horn()
    ok(f"Gegenstelle fuehrt -> node -> wir fuehren, Erfolg: eine Abbuchung, kein Guthaben ({s}, Topf {pair_n()}, {SENT}, "
       f"{len(CL._BACK)})", s == 200 and pair_n() == 1 and SENT == ["dmhorn_lead", "dmhorn"] and not CL._BACK)

    # --- 429 der Gegenstelle: keine Rueckgabe -------------------------------------------------------------------------
    setup(dmhorn=(429, {"error": "too many calls"}))
    s, _ = horn()
    ok(f"429 der Gegenstelle: 429, Abbuchung bleibt, kein Guthaben ({s}, {pair_n()}, {len(CL._BACK)})", s == 429 and pair_n() == 1 and not CL._BACK)

    # --- calls.rate_call (auch lokal api_call/_dm_horn_start): abgelehnte Versuche zaehlen in keinem Topf ----------------
    setup()
    r = [CL.rate_call("u1", "u2") for _ in range(4)] + [CL.rate_call("u1", "u3") for _ in range(3)] + [CL.rate_call("u1", "u4")]
    ok(f"rate_call: 3 je Person, 429 an Person 2 zaehlt nicht im Konto-Topf (6/min) ({r})", r == [True] * 3 + [False] + [True] * 3 + [False])
    setup()
    r = [CL.rate_call("u1", "u2", direct=True) for _ in range(4)]
    ok(f"rate_call direct ohne Rueckfall: wie bisher 3 je Minute ({r})", r == [True] * 3 + [False])

    n = sum(RES)
    print(f"\nANRUFE-RATE {n}/{len(RES)} - " + ("ALLES GRUEN" if n == len(RES) else "FEHLER"))
    sys.exit(0 if n == len(RES) else 1)


if __name__ == "__main__":
    main()
