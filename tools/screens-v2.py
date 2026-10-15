"""Bilder der 2.0-Funktionen fuer die Projektseite (docs/index.html, Abschnitt "Neu in 2.0"): Live/Glocke, Chats,
Mitteilungen, KI-Assistent, Foederation, Teilen & Kommentare. Playwright/Chromium headless in einem Wegwerf-Container
im Docker-Netz eines eigenen Stacks mit zwei Instanzen (A = saga.example, B = saga-b.example) und einer KI-Attrappe.
Umgebung: A, B (Basis-Adressen), SHOT_PW (Passwort aller Demo-Konten), OUT (Zielordner, Standard /out).
Nur erfundene Demo-Inhalte; Wiederherstellungsschluessel erscheinen in keinem Bild."""
import json
import os
import re
import traceback
from playwright.sync_api import sync_playwright

A, B, OUT, PW = os.environ["A"], os.environ["B"], os.environ.get("OUT", "/out"), os.environ["SHOT_PW"]
H = {"X-Saga": "1", "Content-Type": "application/json"}
# http-Testadressen als "sicher" behandeln (Service Worker/Web Push); neuer Headless-Modus, sonst fehlt der Service Worker
ARGS = ["--unsafely-treat-insecure-origin-as-secure=" + ",".join((A, B)), "--headless=new"]
DESK = {"viewport": {"width": 1440, "height": 900}, "device_scale_factor": 2}
PHONE = {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 3, "is_mobile": True, "has_touch": True}


def shot(page, name, wait=800, clip=None):
    page.wait_for_timeout(wait)
    page.screenshot(path=f"{OUT}/{name}.png", clip=clip)
    print("shot", name, flush=True)


def scene(name, fn, *a):
    try:
        fn(*a)
    except Exception:  # noqa: BLE001 - eine Szene darf die anderen nicht verhindern
        print("FEHLER in", name, flush=True)
        traceback.print_exc()


class Who:
    """Eine Person in einem eigenen Browser-Kontext (= eigenes Geraet)."""

    def __init__(self, b, base, user, scheme="dark", form=DESK, state=None):
        self.base, self.user = base, user
        self.ctx = b.new_context(color_scheme=scheme, locale="de-DE", timezone_id="Europe/Berlin", storage_state=state, **form)
        self.ctx.grant_permissions(["notifications"], origin=base)
        self.p = self.ctx.new_page()
        self.p.on("pageerror", lambda e: print(user, "pageerror", str(e)[:200], flush=True))
        if not state:
            self.p.goto(base + "/login")
            self.p.fill("input[name=username]", user)
            self.p.fill("input[name=password]", PW)
            self.p.click("button[type=submit]")
            self.p.wait_for_url(base + "/")

    def api(self, method, path, body=None):
        r = self.p.request.fetch(self.base + path, method=method, headers=H, data=json.dumps(body) if body is not None else None)
        if not r.ok:
            print("API", method, path, r.status, r.text()[:200], flush=True)
            return {}
        try:
            return r.json()
        except Exception:  # noqa: BLE001
            return {}

    def page(self, title, icon, bl, parent=None):
        nid = self.api("POST", "/api/notes", {"title": title, **({"parent_id": parent} if parent else {})})["id"]
        self.api("PATCH", f"/api/notes/{nid}", {"icon": icon, "format": "blocks", "body": blocks(*bl)})
        return nid

    def notes(self, nid, wait=1800):
        self.p.goto(f"{self.base}/notes#{nid}")
        self.p.wait_for_load_state("networkidle")
        self.p.wait_for_timeout(wait)


def blocks(*bl):
    """(id, typ, daten) -> Editor.js-JSON mit festen Block-IDs (fuer Kommentare am Block)."""
    return json.dumps({"blocks": [{"id": i, "type": t, "data": d} for i, t, d in bl]})


def li(*items, style="unordered", checks=None):
    return {"style": style, "meta": {}, "items": [{"content": x, "meta": ({"checked": checks[i]} if checks else {}), "items": []}
                                                   for i, x in enumerate(items)]}


URLAUB = [
    ("u1", "header", {"text": "Der Plan", "level": 2}),
    ("u2", "paragraph", {"text": "Wir fahren am <b>2. August</b> los und bleiben zwei Wochen in Malcesine. "
                                 "Anreise mit dem Auto über den Brenner, Pause in Sterzing."}),
    ("u3", "paragraph", {"text": "Die Ferienwohnung ist angezahlt – die Restzahlung ist bis <b>15. Juli</b> fällig."}),
    ("u4", "header", {"text": "Packliste", "level": 3}),
    ("u5", "list", li("Badesachen und Handtücher", "Wanderschuhe", "Ladekabel &amp; Powerbank", "Badeschuhe", "Sonnencreme",
                      style="checklist", checks=[True, True, True, False, False])),
    ("u8", "paragraph", {"text": "Noch besorgen: Badeschuhe und Sonnencreme."}),
    ("u6", "header", {"text": "Ausflüge", "level": 3}),
    ("u7", "table", {"withHeadings": True, "content": [["Tag", "Ausflug"], ["Montag", "Radtour nach Riva 🚲"],
                                                       ["Mittwoch", "Seilbahn auf den Monte Baldo"], ["Freitag", "Tagesausflug nach Verona"]]}),
    ("u9", "paragraph", {"text": "Für Verona vorher Karten für die Arena reservieren."}),
]
RAEDER = [
    ("r1", "paragraph", {"text": "Die Räder müssen vor dem Urlaub zur Inspektion – Termin bei Rad Huber am 20. Juli."}),
    ("r2", "paragraph", {"text": "Den Dachträger leihen wir uns von Robin, er bringt ihn am Wochenende vorbei."}),
    ("r3", "list", li("Schläuche flicken", "Kettenöl", "Fahrradschlösser", style="checklist", checks=[True, False, False])),
]
HUETTE = [
    ("h1", "header", {"text": "Hüttenwochenende im Allgäu", "level": 2}),
    ("h2", "paragraph", {"text": "Vom 10. bis 12. Oktober auf der Alpe am Grünten. Anreise Freitag ab 15 Uhr, "
                                 "Schlüssel liegt beim Hüttenwirt."}),
    ("h3", "header", {"text": "Wer bringt was mit?", "level": 3}),
    ("h4", "table", {"withHeadings": True, "content": [["Was", "Wer"], ["Brotzeit für Freitag", "Jo"], ["Kässpatzen-Zutaten", "Alex"],
                                                       ["Brettspiele 🎲", "Kim"], ["Getränke", "alle"]]}),
    ("h5", "paragraph", {"text": "Bei Regen: Therme in Oberstdorf statt Gipfeltour."}),
]


def setup_chat(w):
    """Chats im Browser einrichten (Schluessel erzeugen); der Wiederherstellungsschluessel wird nie fotografiert."""
    p = w.p
    p.goto(w.base + "/chats")
    p.wait_for_selector(".ct-setup-go", timeout=20000).click()
    rk = p.inner_text("#ct-rk").strip()
    p.get_by_role("button", name="Weiter").click()
    p.fill(".ct-rk-last", rk[-4:])
    p.click(".ct-setup-done")
    p.wait_for_selector(".ct-setup-done", state="detached", timeout=30000)
    p.wait_for_timeout(500)


def send(w, text):
    w.p.fill("#ct-input", text)
    w.p.click("#ct-send")
    w.p.wait_for_timeout(700)


def open_chat(w, cid):
    w.p.goto(f"{w.base}/chats#c{cid}")
    w.p.wait_for_selector("#ct-chat:not([hidden])", timeout=20000)
    w.p.wait_for_timeout(800)


def chat_id(w):
    m = re.search(r"#c(\d+)", w.p.url)
    return int(m.group(1)) if m else None


def new_dm(w, q, name):
    w.p.goto(w.base + "/chats")
    w.p.wait_for_selector("#ct-new").click()
    w.p.fill(".ct-people-q", q)
    w.p.locator(".ct-person", has_text=name).first.click(timeout=15000)
    w.p.wait_for_selector("#ct-chat:not([hidden])", timeout=20000)
    w.p.wait_for_timeout(600)
    return chat_id(w)


def content(alex, kim, robin, jo):
    """Demo-Inhalte: Seiten, Freigaben (auch an name@host der anderen Instanz), Kommentare."""
    alex.api("PUT", "/api/account/public-name", {"name": "alex"})
    jo.api("PUT", "/api/account/public-name", {"name": "jo"})
    home = alex.page("Willkommen bei Saga", "📖", [("w1", "paragraph", {"text": "Dein eigener Bereich – Seiten, Datenbanken, Aufgaben und Chats."})])
    for t, ic in (("Rezepte", "🍝"), ("Homelab", "🖥️")):
        alex.page(t, ic, [], parent=home)
    urlaub = alex.page("Urlaub Gardasee 2026", "🏖️", URLAUB)
    raeder = alex.page("Fahrräder & Ausrüstung", "🚲", RAEDER)
    for typ, tg, role in (("user", "kim", "edit"), ("group", "familie", "comment"), ("remote", "jo@saga-b.example", "comment")):
        alex.api("PUT", f"/api/notes/{urlaub}/shares", {"type": typ, "target": tg, "role": role})
    alex.api("PUT", f"/api/notes/{raeder}/shares", {"type": "user", "target": "kim", "role": "edit"})
    return urlaub, raeder


def content_late(alex, kim, robin, jo, urlaub):
    """Nach den Chats, damit Kommentare und Freigaben in der Glocke oben stehen."""
    grill = robin.page("Grillabend Samstag", "🔥", [("g1", "paragraph", {"text": "Ab 18 Uhr bei uns im Garten – bringt gern Salate mit."})])
    robin.api("PUT", f"/api/notes/{grill}/shares", {"type": "group", "target": "familie", "role": "view"})
    huette = jo.page("Hüttenwochenende Allgäu", "🏔️", HUETTE)
    jo.api("PUT", f"/api/notes/{huette}/shares", {"type": "remote", "target": "alex@saga.example", "role": "edit"})
    t = kim.api("POST", f"/api/notes/{urlaub}/comments", {"block_id": "u3", "body": "Hab die Restzahlung eben überwiesen ✔"})
    if t.get("thread_id"):
        alex.api("POST", f"/api/notes/{urlaub}/comments", {"thread_id": t["thread_id"], "body": "Super, danke dir! Dann ist das erledigt."})
    robin.api("POST", f"/api/notes/{urlaub}/comments", {"block_id": "u8", "body": "Sonnencreme bringe ich mit, hab noch zwei Flaschen 🙂"})
    kim.api("POST", f"/api/notes/{urlaub}/comments", {"block_id": "u9", "body": "Verona lieber am Donnerstag? Freitags ist es dort sehr voll."})
    return huette


def chats(alex, kim, robin, urlaub):
    for w in (alex, kim, robin):
        setup_chat(w)
    # Gruppen-Chat der Familie und Seiten-Chat zuerst (stehen in der Liste weiter unten)
    alex.p.goto(alex.base + "/chats")
    alex.p.wait_for_selector("#ct-new").click()
    alex.p.fill(".ct-people-q", "familie")
    alex.p.locator(".ct-person", has_text="familie").first.click(timeout=15000)
    alex.p.wait_for_selector("#ct-chat:not([hidden])", timeout=20000)
    fam = chat_id(alex)
    send(alex, "Wer kommt Samstag zum Grillen? 🔥")
    open_chat(robin, fam)
    send(robin, "Bin dabei und bringe Würstchen mit!")
    alex.p.goto(f"{alex.base}/chats#p{urlaub}")
    alex.p.wait_for_selector("#ct-chat:not([hidden])", timeout=20000)
    alex.p.wait_for_timeout(600)
    send(alex, "Packliste ist aktualisiert – schaut bitte mal drüber.")
    dm = new_dm(alex, "Kim", "Kim Beispiel")
    open_chat(kim, dm)
    send(kim, "Hast du die Ferienwohnung am Gardasee schon angezahlt? 🏖️")
    open_chat(alex, dm)
    send(alex, "Ja, letzte Woche. Die Restzahlung ist bis 15. Juli fällig.")
    open_chat(kim, dm)
    send(kim, "Hab ich eben überwiesen ✔ Steht auch als Kommentar auf der Seite.")
    open_chat(alex, dm)
    send(alex, "Perfekt, danke! Dann fehlen nur noch die Räder.")
    open_chat(kim, dm)
    send(kim, "Robin bringt den Dachträger am Wochenende vorbei 👍")
    return dm


def sc_chat(alex, dm):
    open_chat(alex, dm)
    shot(alex.p, "v2-chat", wait=1200)
    alex.p.click("#ct-more")
    alex.p.get_by_text("Sicherheitsnummer", exact=True).first.click()
    alex.p.wait_for_selector("#ct-safety")
    shot(alex.p, "v2-chat-sicherheit", wait=600)
    alex.p.keyboard.press("Escape")


def sc_pair(b, alex, dm):
    """Zweites Geraet (Handy) koppeln: Code auf dem Handy, Bestaetigung am Desktop -> danach Handy-Bilder."""
    ph = Who(b, alex.base, "demo", form=PHONE)
    ph.p.goto(ph.base + "/chats")
    ph.p.wait_for_selector(".ct-link-go", timeout=20000).click()
    ph.p.wait_for_timeout(1200)
    alex.p.goto(alex.base + "/chats")
    alex.p.wait_for_selector(".ct-approve", timeout=30000)
    shot(alex.p, "v2-chat-kopplung", wait=600)
    alex.p.click(".ct-approve")
    ph.p.wait_for_selector(".ct-link-go", state="detached", timeout=30000)
    ph.p.wait_for_timeout(1500)
    open_chat(ph, dm)
    shot(ph.p, "v2-handy-chat", wait=1200)
    return ph


def sc_live(alex, kim, robin, urlaub):
    """Seite mit Anwesenheit: Kim bearbeitet gerade, Robin liest mit."""
    robin.notes(urlaub, wait=800)
    kim.notes(urlaub, wait=800)
    kim.p.locator('.ce-block[data-id="u8"] .ce-paragraph').click()
    kim.p.keyboard.press("End")
    alex.notes(urlaub)
    alex.p.wait_for_selector("#pg-presence", timeout=15000)
    shot(alex.p, "v2-live", wait=2500)


def sc_phone_bell(ph, urlaub):
    ph.notes(urlaub)
    ph.p.click("#hk-bell")
    ph.p.wait_for_selector("#lv-panel:not([hidden])")
    shot(ph.p, "v2-handy-glocke", wait=1500)


def sc_comments_share(alex, urlaub):
    alex.notes(urlaub)
    alex.p.click("#pg-cm-btn")
    shot(alex.p, "v2-kommentare", wait=1200)
    alex.p.keyboard.press("Escape")
    alex.notes(urlaub)
    alex.p.click("#pg-share")
    shot(alex.p, "v2-teilen", wait=1500)
    alex.p.keyboard.press("Escape")


def sc_ai(w, urlaub, name_ask="v2-ki", name_page="v2-ki-seite"):
    w.notes(urlaub)
    w.p.click("#ai-btn")
    w.p.wait_for_selector("#ai-panel:not([hidden]) textarea")
    if name_ask:
        w.p.fill("#ai-panel textarea", "Was müssen wir vor dem Urlaub am Gardasee noch erledigen?")
        w.p.keyboard.press("Enter")
        w.p.wait_for_timeout(4000)
        shot(w.p, name_ask, wait=600)
    if name_page:
        w.p.locator(".ai-tab", has_text="Diese Seite").click()
        w.p.locator(".ai-act", has_text="Zusammenfassen").click()
        w.p.wait_for_timeout(4000)
        shot(w.p, name_page, wait=600)


def sc_fed(alex, huette_ref):
    alex.notes(huette_ref, wait=2500)
    shot(alex.p, "v2-foederation", wait=800)


def sc_push(w, urlaub):
    w.notes(urlaub)
    w.p.click("#hk-bell")
    w.p.click(".lv-gear")
    w.p.wait_for_selector("#hk-tab-push", timeout=15000).click()
    shot(w.p, "v2-mitteilungen", wait=1800)


with sync_playwright() as pw:
    b = pw.chromium.launch(args=ARGS)
    alex, kim, robin = (Who(b, A, u) for u in ("demo", "kim", "robin"))
    jo = Who(b, B, "jo")
    urlaub, raeder = content(alex, kim, robin, jo)
    dm, ph = None, None
    try:
        dm = chats(alex, kim, robin, urlaub)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        shot(alex.p, "debug-chat")
    content_late(alex, kim, robin, jo, urlaub)
    shared = alex.api("GET", "/api/fed/shared")
    href = next((x["id"] for x in shared.get("items") or [] if "Hütten" in str(x.get("title"))), None)
    print("fremde Seite:", href, flush=True)
    if dm:
        scene("chat", sc_chat, alex, dm)
        try:
            ph = sc_pair(b, alex, dm)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
    scene("live", sc_live, alex, kim, robin, urlaub)
    if ph:
        scene("handy-glocke", sc_phone_bell, ph, urlaub)
    scene("kommentare", sc_comments_share, alex, urlaub)
    if href:
        scene("foederation", sc_fed, alex, href)
    scene("ki", sc_ai, alex, urlaub, "v2-ki", None)
    light = Who(b, A, "demo", scheme="light")
    scene("mitteilungen", sc_push, light, urlaub)
    light.p.keyboard.press("Escape")
    scene("ki-seite", sc_ai, light, urlaub, None, "v2-ki-seite")
    b.close()
