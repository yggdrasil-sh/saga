"""Chat-Geraetegrenze beim Koppeln (2.5, Entscheidung des Maintainers 8.11.2026).

Fund: mit 10 Chat-Geraeten blieb das neue Geraet nach "Mit anderem Geraet bestaetigen" im Bestaetigungsschritt haengen
(die Bestaetigung scheiterte still, die Anfrage blieb "pending"). Jetzt:
- GET /api/chat/me: limits.devices_count neben devices_max, Geraete mit last_seen;
- POST /api/chat/link und POST /api/chat/devices an der Grenze: 409 {error, code: device_limit, max, count};
- Grenze erst beim Bestaetigen erreicht: POST /api/chat/link/{id} -> 409 device_limit, die Anfrage verschwindet aus der
  Liste und das neue Geraet bekommt beim Abholen {state: failed, code: device_limit, max, count, error} (einmalig);
- Meldung in der Sprache der Anfrage. Wegwerf-Konto gg-*; raeumt auf. Umgebung wie die anderen Tests."""
import base64, http.client, json, os, sys, time, urllib.parse
from itsdangerous import TimestampSigner
import auth
from db import connect
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chatlib as C  # noqa: E402

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
PW = "Grenze-Pass-1234"
U = "gg-a"
res = []
n5xx = [0]


def ok(n, c, info=""):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, "" if c else info, flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, body=None, lang="de"):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=30)
    h = {"Host": HOST, "Accept-Language": lang, "Cookie": "saga=" + ck(U), "X-Saga": "1"}
    raw = None
    if body is not None:
        h["Content-Type"] = "application/json"; raw = json.dumps(body)
    c.request(m, p, body=raw, headers=h)
    r = c.getresponse(); data = r.read()
    if r.status >= 500:
        n5xx[0] += 1
    try:
        data = json.loads(data)
    except ValueError:
        data = data.decode("utf-8", "replace")
    c.close()
    return r.status, data


def cleanup():
    with connect() as c, c:
        c.execute("DELETE FROM chat_link WHERE uid LIKE 'gg-%'")
        c.execute("DELETE FROM chat_device WHERE uid LIKE 'gg-%'")
        c.execute("DELETE FROM chat_identity WHERE uid LIKE 'gg-%'")
        c.execute("DELETE FROM user_kv WHERE uid LIKE 'gg-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'gg-%'")


def link_body(dev):
    return {"id": dev["id"], "name": "Neues Geraet", "enc_pub": C.b64(dev["enc_pk"]), "sign_pub": C.b64(dev["sign_pk"])}


def approve_body(ident, dev):
    return {"sig": C.b64(C.sign(C.device_msg(U, dev["id"], dev["enc_pk"], dev["sign_pk"]), ident["ident_sk"])),
            "sealed": C.b64(os.urandom(80))}


def limit_ok(d, n=10):
    return isinstance(d, dict) and d.get("code") == "device_limit" and d.get("max") == 10 and d.get("count") == n and bool(d.get("error"))


def main():
    cleanup()
    prev = auth.settings(fresh=True).get("chat_enabled")
    auth.save_setting("chat_enabled", True)
    try:
        auth.create_user(U, PW, "Gerda Grenze")
        run()
    finally:
        cleanup()
        if prev is None:
            with connect() as c, c:
                c.execute("DELETE FROM setting WHERE k='chat_enabled'")
        else:
            auth.save_setting("chat_enabled", prev)
    ok("Keine Serverfehler (HTTP 5xx)", n5xx[0] == 0, n5xx[0])
    good = sum(res)
    print(f"CHAT-GERAETEGRENZE {good}/{len(res)} - " + ("ALLES GRUEN" if good == len(res) else "FEHLER"), flush=True)
    sys.exit(0 if good == len(res) else 1)


def run():
    ident, first = C.identity(), C.device()
    s, d = req("PUT", "/api/chat/identity", C.setup_body(U, ident, first, ops=1, mem=8 << 20))
    ok("Chats eingerichtet (1 Geraet)", s == 200, (s, d))
    devs = [first]
    for _ in range(8):
        dv = C.device()
        s, d = req("POST", "/api/chat/devices", C.device_api(U, ident, dv))
        devs.append(dv)
    s, me = req("GET", "/api/chat/me")
    ok("/api/chat/me: devices_count 9, devices_max 10, last_seen je Geraet",
       s == 200 and me["limits"]["devices_count"] == 9 and me["limits"]["devices_max"] == 10
       and all(x.get("last_seen") for x in me["devices"]), me.get("limits"))

    # 10. Geraet regulaer koppeln (passt noch)
    d10 = C.device()
    s, lk = req("POST", "/api/chat/link", link_body(d10))
    ok("Kopplung bei 9 Geraeten -> 200", s == 200 and lk.get("id"), (s, lk))
    s, d = req("POST", f"/api/chat/link/{lk['id']}", approve_body(ident, d10))
    s2, st = req("GET", f"/api/chat/link/{lk['id']}")
    ok("Bestaetigt -> neues Geraet holt approved ab", s == 200 and st.get("state") == "approved", (s, d, st))

    # an der Grenze: schon das Anlegen der Anfrage scheitert mit klarem Fehler
    d11 = C.device()
    s, d = req("POST", "/api/chat/link", link_body(d11))
    ok("Kopplung bei 10 Geraeten -> 409 device_limit (max 10, count 10)", s == 409 and limit_ok(d), (s, d))
    ok("Meldung nennt Zahl und „Chats & Geräte“", "10" in d.get("error", "") and "Chats & Geräte" in d.get("error", ""), d)
    s, d = req("POST", "/api/chat/link", link_body(d11), lang="en")
    ok("Meldung in der Sprache der Anfrage (en)", s == 409 and limit_ok(d) and d["error"].startswith("You already have"), d)
    s, d = req("POST", "/api/chat/devices", C.device_api(U, ident, d11))
    ok("Wiederherstellungsweg bei 10 Geraeten -> 409 device_limit", s == 409 and limit_ok(d), (s, d))

    # Grenze erst beim Bestaetigen erreicht (Anfrage lief schon): Fehler beim Bestaetigen, neues Geraet erfaehrt es
    s, _ = req("DELETE", f"/api/chat/devices/{devs[1]['id']}")
    s, lk = req("POST", "/api/chat/link", link_body(d11))
    ok("nach Entfernen (9 Geraete) -> Anfrage moeglich", s == 200 and lk.get("id"), (s, lk))
    s, _ = req("POST", "/api/chat/devices", C.device_api(U, ident, C.device()))
    ok("inzwischen ein anderes Geraet dazu (10)", s == 200)
    s, d = req("POST", f"/api/chat/link/{lk['id']}", approve_body(ident, d11))
    ok("Bestaetigen an der Grenze -> 409 device_limit", s == 409 and limit_ok(d), (s, d))
    s, ll = req("GET", "/api/chat/link")
    ok("gescheiterte Anfrage nicht mehr in der Liste offener Anfragen", s == 200 and not any(x["id"] == lk["id"] for x in ll["links"]), ll)
    s, me = req("GET", "/api/chat/me")
    ok("/api/chat/me: links 0, devices_count 10", me["links"] == 0 and me["limits"]["devices_count"] == 10, (me["links"], me["limits"]))
    s, st = req("GET", f"/api/chat/link/{lk['id']}")
    ok("neues Geraet: state failed mit code/max/count/error (statt pending)", s == 200 and st.get("state") == "failed" and limit_ok(st), st)
    s, st = req("GET", f"/api/chat/link/{lk['id']}")
    ok("danach gone (einmalig gemeldet)", st.get("state") == "gone", st)
    s, d = req("POST", f"/api/chat/link/{lk['id']}", approve_body(ident, d11))
    ok("erneutes Bestaetigen der gescheiterten Anfrage -> 404", s == 404, (s, d))
    with connect() as c:
        n = c.execute("SELECT COUNT(*) FROM chat_device WHERE uid=? AND revoked_at IS NULL", (U,)).fetchone()[0]
    ok("nie mehr als 10 aktive Geraete", n == 10, n)


if __name__ == "__main__":
    main()
