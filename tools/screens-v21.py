"""Bilder der 2.1-Funktionen fuer die Projektseite (docs/index.html, Abschnitt "Neu in 2.1"): Anmeldung & Sicherheit
(Passkey + Authenticator-App), Anmeldung mit Passkey, Profilbilder, Audioanruf (Desktop + Handy), Chat mit
Vorschaubildern, Immich im Chat, Admin "Anrufe". Playwright/Chromium headless in einem Wegwerf-Container, der das
Netz des Saga-Containers teilt (BASE_URL = http://localhost - Passkeys brauchen einen sicheren Kontext).
Kamera/Mikrofon sind Attrappen, Passkeys ein virtueller Authenticator (CDP), Immich wird im Browser nachgestellt.
Umgebung: A (Basis, Standard http://localhost), ADM (Admin, Standard http://localhost:8081), SHOT_PW (Passwort aller
Demo-Konten), OUT (Zielordner, Standard /out). Nur erfundene Demo-Inhalte und gezeichnete Bilder; Wiederherstellungs-
schluessel und -codes erscheinen in keinem Bild."""
import base64
import hashlib
import hmac
import json
import os
import struct
import time
import traceback
from playwright.sync_api import sync_playwright

A = os.environ.get("A", "http://localhost")
ADM = os.environ.get("ADM", "http://localhost:8081")
OUT, PW = os.environ.get("OUT", "/out"), os.environ["SHOT_PW"]
TMP = "/tmp/shots-v21"
H = {"X-Saga": "1", "Content-Type": "application/json"}
ARGS = ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--headless=new"]
DESK = {"viewport": {"width": 1440, "height": 900}, "device_scale_factor": 2}
PHONE = {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 3, "is_mobile": True, "has_touch": True}
RELABEL = """() => document.querySelectorAll('select option').forEach(o => {
  if (/^Fake /.test(o.textContent)) o.textContent = 'Lautsprecher (Standard)'; })"""
NO_AUTOFILL = "if (window.PublicKeyCredential) PublicKeyCredential.isConditionalMediationAvailable = () => Promise.resolve(false);"


def shot(page, name, wait=800, full=False):
    page.wait_for_timeout(wait)
    page.screenshot(path=f"{OUT}/{name}.png", full_page=full)
    print("shot", name, flush=True)


def scene(name, fn, *a):
    try:
        return fn(*a)
    except Exception:  # noqa: BLE001 - eine Szene darf die anderen nicht verhindern
        print("FEHLER in", name, flush=True)
        traceback.print_exc()
        return None


class Who:
    """Eine Person in einem eigenen Browser-Kontext (= eigenes Geraet)."""

    def __init__(self, b, user, scheme="dark", form=DESK, login=True):
        self.base, self.user = A, user
        self.ctx = b.new_context(color_scheme=scheme, locale="de-DE", timezone_id="Europe/Berlin", **form)
        self.ctx.grant_permissions(["notifications", "camera", "microphone"], origin=A)
        self.p = self.ctx.new_page()
        self.p.on("pageerror", lambda e: print(user, "pageerror", str(e)[:200], flush=True))
        if login:
            self.login()

    def login(self):
        self.p.goto(A + "/login")
        self.p.fill("input[name=username]", self.user)
        self.p.fill("input[name=password]", PW)
        self.p.click("button[type=submit]")
        self.p.wait_for_load_state("networkidle")

    def api(self, method, path, body=None, raw=None, ctype=None):
        hd = dict(H)
        if raw is not None:
            hd["Content-Type"] = ctype
        r = self.p.request.fetch(A + path, method=method, headers=hd, data=raw if raw is not None else
                                 (json.dumps(body) if body is not None else None))
        if not r.ok:
            print("API", method, path, r.status, r.text()[:200], flush=True)
            return {}
        try:
            return r.json()
        except Exception:  # noqa: BLE001
            return {}

    def page(self, title, icon, bl):
        nid = self.api("POST", "/api/notes", {"title": title})["id"]
        self.api("PATCH", f"/api/notes/{nid}", {"icon": icon, "format": "blocks", "body": blocks(*bl)})
        return nid

    def notes(self, nid, wait=1800):
        self.p.goto(f"{A}/notes#{nid}")
        self.p.wait_for_load_state("networkidle")
        self.p.wait_for_timeout(wait)


def blocks(*bl):
    return json.dumps({"blocks": [{"id": i, "type": t, "data": d} for i, t, d in bl]})


def li(*items, checks=None):
    return {"style": "checklist", "meta": {}, "items": [{"content": x, "meta": {"checked": checks[i]}, "items": []} for i, x in enumerate(items)]}


URLAUB = [
    ("u1", "header", {"text": "Der Plan", "level": 2}),
    ("u2", "paragraph", {"text": "Wir fahren am <b>2. August</b> los und bleiben zwei Wochen in Malcesine. "
                                 "Anreise mit dem Auto über den Brenner, Pause in Sterzing."}),
    ("u3", "paragraph", {"text": "Die Ferienwohnung ist angezahlt – die Restzahlung ist bis <b>15. Juli</b> fällig."}),
    ("u4", "header", {"text": "Packliste", "level": 3}),
    ("u5", "list", li("Badesachen und Handtücher", "Wanderschuhe", "Ladekabel &amp; Powerbank", "Badeschuhe", "Sonnencreme",
                      checks=[True, True, True, False, False])),
    ("u8", "paragraph", {"text": "Noch besorgen: Badeschuhe und Sonnencreme."}),
    ("u6", "header", {"text": "Ausflüge", "level": 3}),
    ("u7", "table", {"withHeadings": True, "content": [["Tag", "Ausflug"], ["Montag", "Radtour nach Riva 🚲"],
                                                       ["Mittwoch", "Seilbahn auf den Monte Baldo"], ["Freitag", "Tagesausflug nach Verona"]]}),
    ("u9", "paragraph", {"text": "Für Verona vorher Karten für die Arena reservieren."}),
]

# Gezeichnete Bilder (Canvas im Browser): Profilbilder (Tier-Emoji auf Verlauf) und drei Urlaubs-"Fotos"
DRAW = r"""
([kind, a, b, c]) => {
  const cv = document.createElement('canvas'), g = cv.getContext('2d');
  if (kind === 'avatar') {
    cv.width = cv.height = 640;
    const gr = g.createLinearGradient(0, 0, 640, 640); gr.addColorStop(0, a); gr.addColorStop(1, b);
    g.fillStyle = gr; g.fillRect(0, 0, 640, 640);
    g.font = '380px "Noto Color Emoji", sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle';
    g.fillText(c, 320, 350);
    return cv.toDataURL('image/png');
  }
  cv.width = 1600; cv.height = 1200;
  const W = 1600, H = 1200, hz = 700;
  const sky = g.createLinearGradient(0, 0, 0, hz); a.forEach((x, i) => sky.addColorStop(i / (a.length - 1), x));
  g.fillStyle = sky; g.fillRect(0, 0, W, hz);
  if (c.sun) { g.fillStyle = c.sun; g.beginPath(); g.arc(c.sx, c.sy, c.sr, 0, 7); g.fill(); }
  const ridge = (col, base, amp, seed, snow) => {
    g.fillStyle = col; g.beginPath(); g.moveTo(0, H);
    const pts = [];
    for (let x = 0; x <= W; x += 40) { const y = base - amp * (0.55 + 0.45 * Math.sin(x / 210 + seed) * Math.cos(x / 97 + seed * 2)); pts.push([x, y]); g.lineTo(x, y); }
    g.lineTo(W, H); g.fill();
    if (snow) { g.fillStyle = snow; pts.forEach(([x, y]) => { if (y < base - amp * 0.82) { g.beginPath(); g.moveTo(x - 30, y + 38); g.lineTo(x, y); g.lineTo(x + 30, y + 38); g.fill(); } }); }
  };
  b.forEach(([col, base, amp, seed, snow]) => ridge(col, base, amp, seed, snow));
  if (c.water) {
    const wg = g.createLinearGradient(0, hz, 0, H); c.water.forEach((x, i) => wg.addColorStop(i / (c.water.length - 1), x));
    g.fillStyle = wg; g.fillRect(0, hz, W, H - hz);
    if (c.sun) { g.fillStyle = c.sun; g.globalAlpha = 0.5; for (let i = 0; i < 14; i++) { const w = 220 - i * 12; g.fillRect(c.sx - w / 2 + (i % 3 - 1) * 14, hz + 18 + i * 28, w, 7); } g.globalAlpha = 1; }
    if (c.boat) { g.fillStyle = '#1d2433'; g.beginPath(); g.moveTo(1010, 860); g.lineTo(1150, 860); g.lineTo(1125, 885); g.lineTo(1035, 885); g.fill();
      g.fillStyle = '#f4f1ea'; g.beginPath(); g.moveTo(1080, 700); g.lineTo(1080, 852); g.lineTo(1150, 852); g.fill(); }
  }
  if (c.meadow) { g.fillStyle = c.meadow; g.beginPath(); g.moveTo(0, 980); g.quadraticCurveTo(800, 820, W, 940); g.lineTo(W, H); g.lineTo(0, H); g.fill();
    g.fillStyle = '#e6e9ef'; g.fillRect(260, 600, 6, 420); g.fillRect(1320, 560, 6, 400);
    g.strokeStyle = '#3d4350'; g.lineWidth = 3; g.beginPath(); g.moveTo(263, 610); g.lineTo(1323, 570); g.stroke();
    g.fillStyle = '#c8433b'; g.fillRect(760, 568, 70, 54); g.fillStyle = '#3d4350'; g.fillRect(793, 548, 4, 22); }
  return cv.toDataURL('image/jpeg', 0.9);
}
"""
AVATARS = {"demo": ("#f6b26b", "#e06666", "🦊"), "kim": ("#9fc5e8", "#6d9eeb", "🐼"),
           "robin": ("#b6d7a8", "#38a169", "🦉"), "sam": ("#d5a6bd", "#8e7cc3", "🐻")}
PHOTOS = {
    "Gardasee": (["#2b2d5c", "#b9506e", "#f39c6b", "#ffd59e"], [["#5b3b5e", 640, 230, 1.0, None], ["#3c2a48", 690, 140, 2.3, None]],
                 {"sun": "#ffe3a3", "sx": 820, "sy": 560, "sr": 90, "water": ["#6a4a6e", "#2c2340"]}),
    "Monte Baldo": (["#3d7bd9", "#9cc8f2", "#e8f3fc"], [["#8a9bb3", 620, 420, 0.4, "#ffffff"], ["#5d6f86", 700, 260, 1.7, "#f5f7fa"]],
                    {"meadow": "#5f9e4b"}),
    "Segeln": (["#5aa6e8", "#bfe2fb"], [["#7c93a8", 690, 120, 0.9, None], ["#5c7a6a", 700, 70, 2.9, None]],
               {"water": ["#2f80c3", "#1b4f7c"], "boat": True}),
}


def draw(p):
    """Bilder einmal zeichnen -> Dateien in TMP; Rueckgabe {name: pfad}"""
    os.makedirs(TMP, exist_ok=True)
    p.set_content("<html><body></body></html>")
    out = {}
    for u, (c1, c2, emo) in AVATARS.items():
        d = p.evaluate(DRAW, ["avatar", c1, c2, emo])
        out["av-" + u] = f"{TMP}/{u}.png"
        open(out["av-" + u], "wb").write(base64.b64decode(d.split(",", 1)[1]))
    for name, (sky, ridges, extra) in PHOTOS.items():
        d = p.evaluate(DRAW, ["photo", sky, ridges, extra])
        out[name] = f"{TMP}/{name}.jpg"
        open(out[name], "wb").write(base64.b64decode(d.split(",", 1)[1]))
    return out


def totp(secret, t=None):
    key = base64.b32decode(secret.replace(" ", "").upper())
    msg = struct.pack(">Q", int((t or time.time()) // 30))
    h = hmac.new(key, msg, hashlib.sha1).digest()
    o = h[-1] & 15
    return "%06d" % ((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 1000000)


# --- Chats -----------------------------------------------------------------------------------------------------------
def setup_chat(w):
    """Chat-Schluessel im Browser einrichten; der Wiederherstellungsschluessel wird nie fotografiert."""
    p = w.p
    p.goto(A + "/chats")
    p.wait_for_selector(".ct-setup-go", timeout=20000).click()
    rk = p.inner_text("#ct-rk").strip()
    p.get_by_role("button", name="Weiter").click()
    p.fill(".ct-rk-last", rk[-4:])
    p.click(".ct-setup-done")
    p.wait_for_selector(".ct-setup-done", state="detached", timeout=30000)
    p.wait_for_timeout(500)


def send(w, text, files=None):
    if files:
        w.p.set_input_files("#ct-file", files)
        w.p.wait_for_selector("#ct-files .ct-chip", timeout=15000)
        w.p.wait_for_timeout(500)
    w.p.fill("#ct-input", text)
    with w.p.expect_response(lambda r: "/messages" in r.url and r.request.method == "POST", timeout=30000):
        w.p.click("#ct-send")
    w.p.wait_for_timeout(900)


def open_chat(w, cid):
    w.p.goto(f"{A}/chats#c{cid}")
    w.p.wait_for_selector("#ct-chat:not([hidden])", timeout=20000)
    w.p.wait_for_timeout(1000)


def chat_id(w):
    import re
    m = re.search(r"#c(\d+)", w.p.url)
    return int(m.group(1)) if m else None


def new_chat(w, q, name):
    w.p.goto(A + "/chats")
    w.p.wait_for_selector("#ct-new").click()
    w.p.fill(".ct-people-q", q)
    w.p.locator(".ct-person", has_text=name).first.click(timeout=15000)
    w.p.wait_for_selector("#ct-chat:not([hidden])", timeout=20000)
    w.p.wait_for_timeout(600)
    return chat_id(w)


def chats(alex, kim, robin, urlaub, img):
    for w in (alex, kim, robin):
        setup_chat(w)
    fam = new_chat(alex, "familie", "familie")
    send(alex, "Wer kommt Samstag zum Grillen? 🔥")
    open_chat(robin, fam)
    send(robin, "Bin dabei und bringe Würstchen mit!")
    alex.p.goto(f"{A}/chats#p{urlaub}")
    alex.p.wait_for_selector("#ct-chat:not([hidden])", timeout=20000)
    alex.p.wait_for_timeout(600)
    send(alex, "Packliste ist aktualisiert – schaut bitte mal drüber.")
    dm = new_chat(alex, "Kim", "Kim Beispiel")
    send(alex, "Seid ihr gut am See angekommen? 🙂")
    open_chat(kim, dm)
    send(kim, "Ja! Schau mal, der Sonnenuntergang gestern 🌅", [img["Gardasee"]])
    open_chat(alex, dm)
    send(alex, "Wow, wie gemalt! Da müssen wir nächstes Jahr auch hin.")
    open_chat(kim, dm)
    send(kim, "Und heute oben auf dem Monte Baldo 🚡", [img["Monte Baldo"]])
    return dm


# --- Szenen ----------------------------------------------------------------------------------------------------------
def avatars_api(ws, img):
    for w in ws:
        r = w.api("PUT", "/api/account/avatar", raw=open(img["av-" + w.user], "rb").read(), ctype="image/png")
        print("avatar", w.user, (r or {}).get("source"), flush=True)


def sc_crop(w, img):
    """Profilbild im Browser zuschneiden (Einstellungen -> Konto), dann speichern."""
    p = w.p
    p.goto(A + "/")
    p.wait_for_timeout(800)
    p.click("#hk-user")
    p.click(".menu-item:has-text('Einstellungen')")
    p.wait_for_selector(".hk-avsec")
    p.wait_for_timeout(500)
    p.set_input_files(".hk-avsec input[type=file]", img["av-demo"])
    p.wait_for_selector(".hk-crop-stage")
    p.locator(".hk-crop input[type=range]").fill("1.25")
    shot(p, "v21-profilbild-zuschneiden", wait=900)
    p.click(".hk-crop .btn-primary")
    p.wait_for_timeout(2500)
    p.keyboard.press("Escape")


def sc_presence(alex, kim, robin, urlaub):
    """Seite mit Profilbildern: Kim und Robin sind auch hier, Kommentare mit Bild."""
    robin.notes(urlaub, wait=800)
    kim.notes(urlaub, wait=800)
    alex.notes(urlaub)
    alex.p.wait_for_selector("#pg-presence", timeout=15000)
    alex.p.click("#pg-cm-btn")
    shot(alex.p, "v21-profilbilder", wait=2500)
    alex.p.keyboard.press("Escape")


def st(p):
    return p.evaluate("() => window.HKCalls && window.HKCalls.state()")


def until(fn, ms=20000):
    end = time.time() + ms / 1000
    v = None
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.3)
    return v


def sc_call(alex, kim, dm):
    """Audioanruf Alex (Desktop, dunkel) -> Kim (Handy, hell): Klingeln, Anruf auf beiden Seiten."""
    kim.p.goto(A + "/chats")
    kim.p.wait_for_timeout(2500)
    open_chat(alex, dm)
    alex.p.click("#ct-call-a")
    kim.p.wait_for_selector(".cl-ring", timeout=20000)
    shot(kim.p, "v21-handy-klingeln", wait=900)
    n0 = len(kim.ctx.pages)
    kim.p.click(".cl-ring .cl-big")
    kim.p.wait_for_timeout(1500)
    kc = kim.ctx.pages[-1] if len(kim.ctx.pages) > n0 else kim.p
    kc.wait_for_load_state("domcontentloaded")
    ok = until(lambda: (st(alex.p) or {}).get("connected") and (st(kc) or {}).get("connected"))
    print("verbunden:", bool(ok), flush=True)
    alex.p.wait_for_timeout(6000)
    for q in (alex.p, kc):                                   # Name der Lautsprecher-Attrappe ("Fake Default ...")
        q.evaluate(RELABEL)
    shot(alex.p, "v21-anruf", wait=300)
    shot(kc, "v21-handy-anruf", wait=300)
    alex.p.click(".cl-ctl .cl-red")
    alex.p.wait_for_timeout(4500)
    if kc is not kim.p:
        kc.close()


def sc_chat_images(alex, kim, dm):
    open_chat(alex, dm)
    alex.p.wait_for_timeout(3000)
    shot(alex.p, "v21-chat-bilder", wait=600)
    open_chat(kim, dm)
    kim.p.wait_for_timeout(3000)
    shot(kim.p, "v21-handy-chat-bilder", wait=600)


def sc_immich(alex, dm, img):
    """Immich im Chat: die Auswahl mit nachgestellter Immich-Antwort (eigene gezeichnete Bilder)."""
    ids = {f"5e1f0000-0000-4000-8000-00000000000{i}": n for i, n in enumerate(list(PHOTOS) * 3)}
    data = {k: open(img[n], "rb").read() for k, n in ids.items()}
    ctx = alex.ctx
    ctx.route("**/api/settings/immich", lambda r: r.fulfill(json={"available": True, "connected": True, "name": "Alex", "url": "https://fotos.example.org"}))
    ctx.route("**/api/immich/assets*", lambda r: r.fulfill(json={"items": [{"id": k, "name": f"IMG_20{i:02d}.jpg", "mime": "image/jpeg"}
                                                                          for i, k in enumerate(ids)], "next": None}))
    ctx.route("**/api/immich/thumb/**", lambda r: r.fulfill(body=next((v for k, v in data.items() if k in r.request.url), b""), content_type="image/jpeg"))
    alex.p.goto(A + "/")                                   # neu laden: die Chat-Seite fragt Immich nur beim Laden ab
    open_chat(alex, dm)
    alex.p.click("#ct-attach")
    alex.p.wait_for_timeout(300)
    alex.p.locator(".menu .menu-item", has_text="Aus Immich").click()
    alex.p.wait_for_selector(".imm-tile")
    shot(alex.p, "v21-immich", wait=2000)
    alex.p.keyboard.press("Escape")


def sc_security(b):
    """Robin (eigener Kontext, dunkel): Passkey "MacBook" + Authenticator-App -> /security; danach Anmeldung mit
    Passwort -> Zwischenschritt "Zweiter Faktor"."""
    w = Who(b, "robin")
    w.p.add_init_script(NO_AUTOFILL)
    cdp = w.ctx.new_cdp_session(w.p)
    cdp.send("WebAuthn.enable")
    cdp.send("WebAuthn.addVirtualAuthenticator", {"options": {"protocol": "ctap2", "transport": "internal", "hasResidentKey": True,
                                                              "hasUserVerification": True, "isUserVerified": True,
                                                              "automaticPresenceSimulation": True}})
    p = w.p
    p.goto(A + "/security")
    p.wait_for_selector("#hk-security .mfa-block")
    p.fill("#hk-security input[aria-label='Name des Passkeys']", "MacBook")
    p.click("#hk-security .mfa-block button:has-text('Passkey hinzufügen')")
    p.wait_for_selector(".mfa-codes li", timeout=15000)
    p.click(".mfa-codes-box button:has-text('Ich habe sie gespeichert')")      # Codes werden nicht fotografiert
    p.wait_for_timeout(800)
    d = w.api("POST", "/api/account/totp/setup")
    if d.get("secret"):
        sec = d["secret"]
        if 25 < time.time() % 30:                                                 # nicht kurz vor dem Wechsel
            time.sleep(6)
        print("totp", bool(w.api("POST", "/api/account/totp/confirm", {"code": totp(sec)})), flush=True)
    p.goto(A + "/security")
    p.wait_for_selector("#hk-security .hk-tok", timeout=15000)
    shot(p, "v21-sicherheit", wait=1200)
    p.goto(A + "/logout")
    w.login()
    p.wait_for_url("**/login/2fa**", timeout=15000)
    shot(p, "v21-login-2fa", wait=1200)
    w.ctx.close()


def sc_login(b):
    """Anmeldeseite mit "Mit Passkey anmelden" (Desktop hell, Handy dunkel)."""
    for name, scheme, form in (("v21-login-passkey", "light", DESK), ("v21-handy-login", "dark", PHONE)):
        w = Who(b, "kim", scheme=scheme, form=form, login=False)
        w.p.add_init_script(NO_AUTOFILL)
        w.p.goto(A + "/login")
        w.p.wait_for_selector("#pk-box:not([hidden])", timeout=10000)
        w.p.fill("input[name=username]", "kim")
        shot(w.p, name, wait=800)
        w.ctx.close()


def sc_admin(b):
    """Admin -> Allgemein: 2FA-Richtlinie und Anrufe (hell)."""
    ctx = b.new_context(color_scheme="light", locale="de-DE", timezone_id="Europe/Berlin", **DESK)
    p = ctx.new_page()
    p.goto(ADM + "/login")
    p.fill("input[name=username]", "demo-admin")
    p.fill("input[name=password]", PW)
    p.click("button[type=submit]")
    p.wait_for_load_state("networkidle")
    p.goto(ADM + "/settings")
    p.wait_for_selector("#anrufe")
    p.evaluate("() => document.getElementById('anrufe').scrollIntoView({block: 'start'})")
    p.evaluate("() => window.scrollBy(0, -24)")
    shot(p, "v21-admin-anrufe", wait=800)
    p.evaluate("() => document.getElementById('mfa').scrollIntoView({block: 'start'})")
    p.evaluate("() => window.scrollBy(0, -24)")
    shot(p, "v21-admin-2fa", wait=800)
    ctx.close()


with sync_playwright() as pw:
    b = pw.chromium.launch(args=ARGS)
    tmp = b.new_page()
    img = draw(tmp)
    tmp.close()
    alex = Who(b, "demo")
    kim = Who(b, "kim", scheme="light", form=PHONE)
    robin = Who(b, "robin")
    avatars_api([kim, robin], img)
    scene("zuschneiden", sc_crop, alex, img)
    alex.api("PUT", "/api/account/public-name", {"name": "alex"})
    urlaub = alex.page("Urlaub Gardasee 2026", "🏖️", URLAUB)
    for typ, tg, role in (("user", "kim", "edit"), ("group", "familie", "comment")):
        alex.api("PUT", f"/api/notes/{urlaub}/shares", {"type": typ, "target": tg, "role": role})
    t = kim.api("POST", f"/api/notes/{urlaub}/comments", {"block_id": "u3", "body": "Hab die Restzahlung eben überwiesen ✔"})
    if t.get("thread_id"):
        alex.api("POST", f"/api/notes/{urlaub}/comments", {"thread_id": t["thread_id"], "body": "Super, danke dir!"})
    robin.api("POST", f"/api/notes/{urlaub}/comments", {"block_id": "u8", "body": "Sonnencreme bringe ich mit 🙂"})
    dm = scene("chats", chats, alex, kim, robin, urlaub, img)
    scene("profilbilder", sc_presence, alex, kim, robin, urlaub)
    if dm:
        scene("anruf", sc_call, alex, kim, dm)
        scene("chat-bilder", sc_chat_images, alex, kim, dm)
        scene("immich", sc_immich, alex, dm, img)
    scene("sicherheit", sc_security, b)
    scene("login", sc_login, b)
    scene("admin", sc_admin, b)
    b.close()
