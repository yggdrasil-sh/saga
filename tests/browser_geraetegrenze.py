"""Browser: Chat-Geraetegrenze in der Weboberflaeche (2.5, Entscheidung des Maintainers 8.11.2026).

Ein Konto mit 10 Chat-Geraeten (ueber die API eingerichtet) oeffnet /chats in einem frischen Browser (keine Schluessel):
- die Einrichtung zeigt sofort den Hinweis auf die Grenze mit Knopf "Chats & Geräte öffnen";
- "Chats & Geräte": "10 von 10 Geräten", ein lange nicht genutztes Geraet ist markiert, "Dieses Gerät einrichten" fuehrt zurueck;
- "Mit anderem Gerät bestätigen" an der Grenze: Hinweis statt Wartecode;
- Grenze erst beim Bestaetigen erreicht (anderes Geraet bestaetigt, Server lehnt ab): das Warten bricht ab, Hinweis erscheint.
Python-Playwright (Chromium, Locale de-DE). Wegwerf-Konto gb2-*; raeumt auf. Umgebung wie die anderen Tests."""
import base64, http.client, json, os, sys, time, urllib.parse
from itsdangerous import TimestampSigner
from playwright.sync_api import sync_playwright
import auth
from db import connect
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chatlib as C  # noqa: E402

BASE = os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099")
B_ = urllib.parse.urlparse(BASE)
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
U = "gb2-a"
res = []


def ok(n, c, info=""):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, "" if c else info, flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 600}).encode())).decode()


def req(m, p, body=None):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=30)
    h = {"Host": HOST, "Accept-Language": "de", "Cookie": "saga=" + ck(U), "X-Saga": "1"}
    raw = json.dumps(body) if body is not None else None
    if raw:
        h["Content-Type"] = "application/json"
    c.request(m, p, body=raw, headers=h)
    r = c.getresponse(); data = r.read(); c.close()
    try:
        return r.status, json.loads(data)
    except ValueError:
        return r.status, data


def cleanup():
    with connect() as c, c:
        for tb in ("chat_link", "chat_device", "chat_identity", "user_kv"):
            c.execute(f"DELETE FROM {tb} WHERE uid LIKE 'gb2-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'gb2-%'")


def main():
    cleanup()
    prev = auth.settings(fresh=True).get("chat_enabled")
    auth.save_setting("chat_enabled", True)
    try:
        auth.create_user(U, "Grenze-Pass-1234", "Greta Browser")
        run()
    finally:
        cleanup()
        if prev is not None:
            auth.save_setting("chat_enabled", prev)
    good = sum(res)
    print(f"BROWSER-GERAETEGRENZE {good}/{len(res)} - " + ("ALLES GRUEN" if good == len(res) else "FEHLER"), flush=True)
    sys.exit(0 if good == len(res) else 1)


def run():
    ident = C.identity()
    devs = [C.device() for _ in range(10)]
    s, d = req("PUT", "/api/chat/identity", C.setup_body(U, ident, devs[0], ops=1, mem=8 << 20))
    for dv in devs[1:]:
        req("POST", "/api/chat/devices", C.device_api(U, ident, dv, name="Altes Tablet" if dv is devs[3] else "Testgeraet"))
    with connect() as c, c:
        c.execute("UPDATE chat_device SET last_seen=? WHERE id=?", (time.time() - 70 * 86400, devs[3]["id"]))
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(locale="de-DE")
        ctx.add_cookies([{"name": "saga", "value": ck(U), "url": BASE}])
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(BASE + "/chats")
        lim = pg.locator(".ct-limit")
        try:
            lim.first.wait_for(timeout=15000)
        except Exception:  # noqa: BLE001
            pass
        ok("Einrichtung an der Grenze: Hinweis sofort sichtbar", lim.count() > 0 and "10 Chat-Geräte" in lim.first.inner_text(),
           pg.locator(".ct-ov, body").first.inner_text()[:300])
        pg.locator(".ct-limit-go").first.click()
        cnt = pg.locator(".ct-dev-count")
        cnt.wait_for(timeout=8000)
        ok("Chats & Geräte: „10 von 10 Geräten“ (rot)", "10 von 10 Geräten" in cnt.inner_text() and "err" in (cnt.get_attribute("class") or ""), cnt.inner_text())
        old = pg.locator(".ct-dev-old")
        ok("lange nicht genutztes Geraet markiert", old.count() == 1 and "Altes Tablet" in old.inner_text()
           and "seit über 60 Tagen nicht genutzt" in old.inner_text(), old.count())
        pg.locator(".ct-dev-setup").click()
        pg.locator(".ct-link-go").click()
        hint = pg.locator(".ct-limit")
        hint.first.wait_for(timeout=8000)
        ok("„Mit anderem Gerät bestätigen“ an der Grenze: Hinweis statt Code", pg.locator("#ct-link-code").count() == 0 and hint.count() > 0)

        # Grenze erst beim Bestaetigen: ein Platz frei, Anfrage laeuft, dann kommt ein anderes Geraet dazu
        req("DELETE", f"/api/chat/devices/{devs[5]['id']}")
        pg.goto(BASE + "/chats")
        pg.locator(".ct-link-go").wait_for(timeout=15000)
        pg.locator(".ct-link-go").click()
        pg.locator("#ct-link-code").wait_for(timeout=8000)
        s, ll = req("GET", "/api/chat/link")
        lk = (ll.get("links") or [{}])[0]
        req("POST", "/api/chat/devices", C.device_api(U, ident, C.device()))
        nd = {"id": lk.get("device_id"), "enc_pk": C.unb64(lk.get("enc_pub")), "sign_pk": C.unb64(lk.get("sign_pub"))}
        s, d = req("POST", f"/api/chat/link/{lk.get('id')}", {"sig": C.b64(C.sign(C.device_msg(U, nd["id"], nd["enc_pk"], nd["sign_pk"]), ident["ident_sk"])),
                                                              "sealed": C.b64(os.urandom(80))})
        ok("anderes Geraet bestaetigt an der Grenze -> 409 device_limit", s == 409 and d.get("code") == "device_limit", (s, d))
        t0 = time.time()
        try:
            pg.locator(".ct-limit").first.wait_for(timeout=10000)
        except Exception:  # noqa: BLE001
            pass
        ok("neues Geraet: Warten bricht ab, Hinweis auf die Grenze", pg.locator(".ct-limit").count() > 0
           and pg.locator("#ct-link-code").count() == 0, round(time.time() - t0, 1))
        ok("keine Skriptfehler", not errs, errs)
        b.close()


if __name__ == "__main__":
    main()
