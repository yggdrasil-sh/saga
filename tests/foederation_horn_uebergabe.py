"""2.5 Anruf-Uebergabe ueber Knoten ueber Servergrenzen (Fund vom 9.11.: iOS -> Web, Kollege sieht/hoert nichts).

Zwei eigene Saga-Instanzen: A ("Mutter", calls.horn_all, teilt ihren Knoten mit B, fuehrt den 1:1) und B ("Partner", ohne
eigenen Knoten). Der Knoten von A ist ein Registereintrag (bereit, Zertifikat "self", Teilen an) ohne echten Prozess -
geprueft wird die Steuerung in Saga (Signalweg, Teilnehmerliste, Kennungen am Knoten), nicht die Medien.

Ursache des Funds: Das uebernehmende Geraet buendelt fkey/state (mit hp = Kennung am Knoten) in EINER Anfrage an alle
Endpunkte seiner Liste. Ging das ersetzte Geraet in derselben Sekunde (altes Bein zu vor dem Zustand des neuen), lehnte
der Server die ganze Anfrage mit 400 ab - Bert bekam weder Schluessel noch Kennung des neuen Geraets: kein Bild, kein Ton.

Geprueft (je Runde neue Kennung je Generation):
- Bert (B) ruft Anna (A) ueber den Knoten von A an (dmhorn_lead), Anna nimmt mit dem Telefon an.
- Vier Uebergaben Telefon <-> Laptop (A1 -> A2 -> A1 -> A2), abwechselnd: altes Bein geht VOR dem Buendel des neuen
  Geraets (Fund) bzw. erst danach (zweiter Versuch vom 9.11., ging). Jedes Mal: Buendel {Bert, altes Geraet} -> 200
  (im ersten Fall mit dropped = [altes Geraet]), bei Bert kommen fkey und state.hp an, hp = Kennung des neuen Geraets in
  der Teilnehmerliste von A; die Kopie auf B fuehrt das neue Geraet und nicht mehr das alte.
- Gegenrichtung: Bert schickt sein Buendel an {altes, neues Geraet} (seine Liste kann kurz veraltet sein) -> 200, das
  neue Geraet bekommt den Schluessel.
- Ein Buendel nur an ein gegangenes Geraet bleibt 400 (keine stillen Leerlaeufe), fremde Ziele sind weiter ungueltig.
Ports 8530-8533, Datenbanken saga_anruf_fa/_fb. Raeumt auf."""
import base64, json, os, secrets, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foederation_horn as FH  # noqa: E402  (Instanzen, Verbinden, Einstellungen)
import chatlib as C  # noqa: E402

T, F2 = FH.T, FH.F2
T.I["a"].update(port=8530, admin=8531, db="saga_anruf_fa", url="http://127.0.0.1:8530", host="127.0.0.1:8530", state="/tmp/st-anruf/fa",
                allow="localhost:8532")
T.I["b"].update(port=8532, admin=8533, db="saga_anruf_fb", url="http://localhost:8532", host="localhost:8532", state="/tmp/st-anruf/fb",
                allow="127.0.0.1:8530")
ok, wait, sql = T.ok, T.wait, T.sql
api, User = FH.api, FH.User
CAPS = FH.CAPS
NODE = "horn-mutter.example.org"
FH.USERS_A[:] = [("fa-anna", "Anna Berg", "anna")]
FH.USERS_B[:] = [("fb-bert", "Bert Gast", "bert")]


def fake_node():
    """Bereiter, geteilter Knoten von A als Registereintrag (Lastmeldung in der Zukunft = bleibt bereit)."""
    F2.py("a", f"""
import time
from db import connect
from saga import horn
with connect() as c, c:
    c.execute("INSERT INTO horn_node (id, name, pub, builtin, url, udp, share, version, state, load, created, seen, cert) "
              "VALUES (?,?,?,0,?,?,1,?,'ok',CAST(? AS JSONB),?,?,CAST(? AS JSONB))",
              ({secrets.token_hex(8)!r}, {NODE!r}, "dGVzdC1rbm90ZW4tbXV0dGVyLTAwMDAwMDAwMDAwMDA=", "https://{NODE}:8443", True, horn.VERSION,
               json.dumps({{"cpu": 0.05, "legs": 0, "max_legs": 48, "state": "ok"}}), time.time(), time.time() + 7200,
               json.dumps({{"mode": "alpn", "state": "self"}})))
print(1)""")


def add_dev(u, name):
    dv = C.device()
    s, d = api(u.k, u.uid, "POST", "/api/chat/devices", C.device_api(u.uid, u.ident, dv, name))
    assert s == 200, (s, d)
    s, me = api(u.k, u.uid, "PUT", "/api/chat/v2/cert", {"devices": {dv["id"]: C.b64(C.device_sig_v2(u.handle, u.ident, dv))}})
    ver = ((me.get("device_set") or {}).get("ver") or 0) + 1
    s, me = api(u.k, u.uid, "PUT", "/api/chat/v2/cert", {"device_set": C.devices_sig_v2(u.handle, u.ident, ver, me["device_set_want"])})
    assert s == 200 and me.get("device_set_ok"), (s, me)
    api(u.k, u.uid, "PUT", "/api/calls/caps", {"device": dv["id"], "caps": CAPS})
    return dv


def seal(cid, frm, fdev, to, tdev, b):
    return C.seal_signal_v2(cid, frm.handle, fdev, to.handle, tdev["id"], tdev["enc_pk"], b)


def got(me, dev, cid, frm, fdev, since=0):
    s, d = api(me.k, me.uid, "GET", f"/api/calls/{cid}?device={dev['id']}")
    out = []
    for x in (d or {}).get("signals", []):
        if x["from_device"] != fdev["id"] or x["seq"] <= since:
            continue
        b, sg = C.open_signal_v2(x["sealed"], dev["enc_pk"], dev["enc_sk"])
        if C.verify_signal_v2(cid, frm.handle, fdev["id"], me.handle, dev["id"], b, sg, fdev["sign_pk"]):
            out.append(json.loads(b))
    return out


def legs(u, cid, dev=None):
    s, d = api(u.k, u.uid, "GET", f"/api/calls/{cid}" + (f"?device={dev['id']}" if dev else ""))
    return {x["device"]: x for x in ((d or {}).get("call") or {}).get("legs", [])} if s == 200 else {}


def fkey():
    return json.dumps({"t": "fkey", "n": 0, "kid": 0, "key": base64.b64encode(secrets.token_bytes(32)).decode(), "s": secrets.token_hex(8)},
                      separators=(",", ":"))


def state(hp):
    return json.dumps({"t": "state", "mic": True, "cam": True, "screen": False, "hp": [hp] if hp else []}, separators=(",", ":"))


def join_ho(anna, dev, rep, cid):
    for _ in range(6):
        s, d = api("a", "fa-anna", "POST", f"/api/calls/{cid}/join", {"device": dev["id"], "role": "handover", "replaces": rep["id"], "caps": CAPS,
                                                                     "dev_class": "desktop"})
        if s != 429:
            return s, d
        time.sleep(10)
    return s, d


def handover(anna, bert, cid, old, new, early, rnd):
    """Uebergabe old -> new. early: altes Bein geht vor dem Buendel des neuen Geraets (Fund 20:50:32)."""
    s, d = join_ho(anna, new, old, cid)
    cands = ((d.get("call") or {}).get("horn") or d.get("horn") or {}).get("candidates") or []
    hp = cands[0]["peer"] if cands else None
    gen_old = (legs(anna, cid).get(old["id"]) or {}).get("gen")
    ok(f"R{rnd}: {new['name']} uebernimmt ({s}, {len(cands)} Kandidat(en))", s == 200 and bool(hp))
    la = legs(anna, cid)
    ok(f"R{rnd}: hp in der Liste von A = Kennung im Kandidaten", (la.get(new["id"]) or {}).get("hp") == hp)
    ok(f"R{rnd}: Kopie auf B fuehrt das neue Geraet", bool(wait(lambda: new["id"] in legs(bert, cid), 5)))
    mark = max([x["seq"] for x in (api("b", "fb-bert", "GET", f"/api/calls/{cid}?device={bert.dev['id']}")[1] or {}).get("signals", [])] or [0])

    def leave_old():
        return api("a", "fa-anna", "POST", f"/api/calls/{cid}/leave", {"device": old["id"], "reason": "handover", "gen": gen_old})
    if early:
        s0, _ = leave_old()
        ok(f"R{rnd}: altes Geraet geht sofort ({s0})", s0 == 200)
    # Buendel wie static/calls.js hornKeys/hornStateOut: an alle Endpunkte der (evtl. veralteten) eigenen Liste
    fk, st = fkey(), state(hp)
    sig = {bert.dev["id"]: seal(cid, anna, new, bert, bert.dev, fk), old["id"]: seal(cid, anna, new, anna, old, fk)}
    s1, d1 = api("a", "fa-anna", "POST", f"/api/calls/{cid}/signal", {"device": new["id"], "signals": sig})
    sig = {bert.dev["id"]: seal(cid, anna, new, bert, bert.dev, st), old["id"]: seal(cid, anna, new, anna, old, st)}
    s2, d2 = api("a", "fa-anna", "POST", f"/api/calls/{cid}/signal", {"device": new["id"], "signals": sig})
    want = [old["id"]] if early else None
    ok(f"R{rnd}: Buendel fkey/state des neuen Geraets an {{Bert, altes}} -> 200 ({s1}/{s2}, dropped {d1.get('dropped')}, {d1.get('error')})",
       s1 == 200 and s2 == 200 and d1.get("dropped") == want and d2.get("dropped") == want)
    rx = wait(lambda: (lambda b: b if any(x["t"] == "fkey" for x in b) and any(x["t"] == "state" for x in b) else None)(
        got(bert, bert.dev, cid, anna, new, mark)), 6) or []
    sth = [x for x in rx if x["t"] == "state"]
    ok(f"R{rnd}: bei Bert kommen fkey und state.hp des neuen Geraets an ({[x['t'] for x in rx]})",
       any(x["t"] == "fkey" for x in rx) and bool(sth) and sth[-1].get("hp") == [hp])
    if not early:
        s0, _ = leave_old()
        ok(f"R{rnd}: altes Geraet geht danach ({s0})", s0 == 200)
    ok(f"R{rnd}: Kopie auf B ohne das alte Geraet", bool(wait(lambda: old["id"] not in legs(bert, cid), 5)))
    # Gegenrichtung: Berts Liste kann das alte Geraet noch enthalten - sein Buendel muss trotzdem ankommen
    mk2 = max([x["seq"] for x in (api("a", "fa-anna", "GET", f"/api/calls/{cid}?device={new['id']}")[1] or {}).get("signals", [])] or [0])
    bk = fkey()
    s3, d3 = api("b", "fb-bert", "POST", f"/api/calls/{cid}/signal", {"device": bert.dev["id"], "signals": {
        new["id"]: seal(cid, bert, bert.dev, anna, new, bk), old["id"]: seal(cid, bert, bert.dev, anna, old, bk)}})
    ok(f"R{rnd}: Bert -> {{neues, altes}} -> 200 ({s3}, {d3.get('error')}, dropped {d3.get('dropped')})", s3 == 200)
    ok(f"R{rnd}: neues Geraet hat Berts Schluessel", bool(wait(lambda: [x for x in got(anna, new, cid, bert, bert.dev, mk2) if x["t"] == "fkey"], 6)))


def main():
    rc = 1
    try:
        os.makedirs("/tmp/st-anruf", exist_ok=True)
        adm = FH.setup(horn_b=True)
        HA, HB = T.I["a"]["host"], T.I["b"]["host"]
        fake_node()
        FH.calls_cfg("a", horn_all=True)
        time.sleep(6)
        FH.calls_post(adm["a"], {"action": "caps"})
        FH.calls_post(adm["b"], {"action": "caps"})
        ok("horn_dm/horn_req bekannt", wait(lambda: FH.has_cap("b", HA, "horn_req") and FH.has_cap("a", HB, "horn_dm"), 20))
        anna, bert = User("a", "fa-anna"), User("b", "fb-bert")
        for u in (anna, bert):
            api(u.k, u.uid, "PUT", "/api/calls/caps", {"device": u.dev["id"], "caps": CAPS})
        s, d = api("a", "fa-anna", "POST", "/api/chats", {"kind": "dm", "handle": bert.handle, "device": anna.dev["id"]})
        ach = d.get("chat") or {}
        bch = wait(lambda: bert.chat(ach.get("ref")), 15) or {}
        api("b", "fb-bert", "POST", f"/api/chats/{bch.get('id')}/request", {"action": "accept"})
        ok("Direkt-Chat ueber zwei Server", s == 200 and bool(wait(
            lambda: (sql("a", "SELECT state FROM chat_member WHERE chat_id=? AND uid LIKE '@%'", (ach["id"],)) or [[None]])[0][0] == "ok", 15)))
        a1 = dict(anna.dev, name="Telefon")
        a2 = dict(add_dev(anna, "Laptop"), name="Laptop")
        cid = C.hexid()
        s, d = api("b", "fb-bert", "POST", "/api/calls", {"id": cid, "chat_id": bch["id"], "media": "video", "device": bert.dev["id"], "caps": CAPS,
                                                          "via": "horn", "dev_class": "mobile"})
        ok(f"Bert (B) ruft Anna an -> ueber den Knoten von A ({s}, {(d.get('call') or {}).get('topo')}, {d.get('horn_block')})",
           s == 200 and (d.get("call") or {}).get("topo") == "horn")
        s, d = api("a", "fa-anna", "POST", f"/api/calls/{cid}/join", {"device": a1["id"], "role": "member", "caps": CAPS, "dev_class": "mobile"})
        ok(f"Anna nimmt mit dem Telefon an ({s})", s == 200)
        ok("aktiv auf A und B", wait(lambda: all((sql(k, "SELECT state FROM chat_call WHERE id=?", (cid,)) or [[0]])[0][0] == "active" for k in "ab"), 10))
        # Fund 20:50:32 (altes zu vor neuem Zustand), 21:02:50 (altes erst nach 3 s), dann schnell hin und her
        handover(anna, bert, cid, a1, a2, True, 1)
        handover(anna, bert, cid, a2, a1, False, 2)
        handover(anna, bert, cid, a1, a2, True, 3)
        handover(anna, bert, cid, a2, a1, True, 4)
        # Ohne ein einziges gueltiges Ziel bleibt es 400; ein fremdes, nie beteiligtes Geraet ebenso
        s, d = api("a", "fa-anna", "POST", f"/api/calls/{cid}/signal", {"device": a1["id"], "signals": {
            a2["id"]: seal(cid, anna, a1, anna, a2, fkey())}})
        ok(f"Buendel nur an ein gegangenes Geraet -> 400 ({s})", s == 400)
        s, d = api("a", "fa-anna", "POST", f"/api/calls/{cid}/signal", {"device": a1["id"], "signals": {bert.dev["id"]: "x"}})
        ok(f"Formfehler im Buendel -> 400 ({s})", s == 400)
        ok("Auflegen beendet auf beiden Seiten", FH.end_call(cid, anna))
        rc = 0
    except Exception as ex:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        ok(f"Ablauf ohne Ausnahme ({type(ex).__name__}: {ex})", False)
    finally:
        T.stop_all()
        for k in T.I:
            n, txt = T.server_errors(k)
            ok(f"{k.upper()}: Serverfehler {n}", n == 0)
            if n:
                print(txt[:3000])
        for k in T.I:
            T.pg_admin(f"DROP DATABASE IF EXISTS {T.I[k]['db']} WITH (FORCE)")
    print(f"\nFOEDERATION-HORN-UEBERGABE {sum(T.res)}/{len(T.res)}", "- ALLES GRUEN" if all(T.res) and rc == 0 else "- FEHLER")
    sys.exit(0 if all(T.res) and rc == 0 else 1)


if __name__ == "__main__":
    main()
