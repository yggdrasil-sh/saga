"""Screenshots fuer README und Anleitung (Playwright/Chromium, headless). Laeuft in einem Wegwerf-Container
im Docker-Netz des Stacks (tools/screens-remote.sh) mit Demo-Konten, die danach wieder geloescht werden."""
import json
import os
from playwright.sync_api import sync_playwright

BASE, ADMIN, OUT = os.environ["BASE"], os.environ["ADMIN"], os.environ.get("OUT", "/out")
PW = os.environ["SHOT_PW"]
H = {"X-Saga": "1", "Content-Type": "application/json"}


def shot(page, name, full=False, wait=800):
    page.wait_for_timeout(wait)
    page.screenshot(path=f"{OUT}/{name}.png", full_page=full)
    print("shot", name)


def blocks(*bl):
    return json.dumps({"blocks": [{"type": t, "data": d} for t, d in bl]})


def li(*items, style="unordered", checks=None):
    return {"style": style, "meta": {}, "items": [{"content": x, "meta": ({"checked": checks[i]} if checks else {}), "items": []}
                                                   for i, x in enumerate(items)]}


def demo_content(pg):
    api = pg.request
    post = lambda p, b: api.post(BASE + p, data=json.dumps(b), headers=H).json()      # noqa: E731
    patch = lambda p, b: api.patch(BASE + p, data=json.dumps(b), headers=H)           # noqa: E731
    home = post("/api/notes", {"title": "Willkommen bei Saga"})["id"]
    patch(f"/api/notes/{home}", {"icon": "📖", "format": "blocks", "body": blocks(
        ("header", {"text": "Dein eigener Bereich", "level": 2}),
        ("paragraph", {"text": "Seiten, Datenbanken, Aufgaben, Kalender und Lesezeichen – <b>alles gehört nur dir</b>. "
                               "Tippe <code class=\"inline-code\">/</code> für neue Blöcke."}),
        ("list", li("Wochenplan anlegen", "Kalender verbinden", "Fotos aus Immich einbinden", style="checklist",
                    checks=[True, True, False])),
        ("quote", {"text": "Erzähl deine Geschichte – Saga merkt sie sich.", "caption": "", "alignment": "left"}),
        ("header", {"text": "Diese Woche", "level": 3}),
        ("table", {"withHeadings": True, "content": [["Tag", "Plan"], ["Montag", "Sprint-Planung"], ["Mittwoch", "Zahnarzt 15:00"],
                                                     ["Freitag", "Spieleabend 🎲"]]}),
    )})
    for t in ("Rezepte", "Reisen", "Homelab"):
        k = post("/api/notes", {"title": t, "parent_id": home})["id"]
        patch(f"/api/notes/{k}", {"icon": {"Rezepte": "🍝", "Reisen": "✈️", "Homelab": "🖥️"}[t]})
    db = post("/api/notes", {"kind": "database", "title": "Projekte"})["id"]
    patch(f"/api/notes/{db}", {"icon": "🗂️"})
    for title, status in (("Website neu", "doing"), ("Garten planen", "todo"), ("Backup testen", "done"), ("Fotobuch 2026", "todo"),
                          ("Umzug Server", "doing")):
        post("/api/notes", {"parent_id": db, "title": title, "props": {"status": status}})
    return home, db


with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900}, color_scheme="dark", locale="de-DE",
                        timezone_id="Europe/Berlin", device_scale_factor=2)
    pg = ctx.new_page()
    pg.goto(BASE + "/login"); shot(pg, "login")
    pg.fill("input[name=username]", "demo"); pg.fill("input[name=password]", PW); pg.click("button[type=submit]")
    pg.wait_for_url(BASE + "/")
    home, db = demo_content(pg)
    pg.goto(BASE + "/"); pg.wait_for_load_state("networkidle"); shot(pg, "dashboard", wait=1500)
    pg.click("#bm-add"); shot(pg, "lesezeichen")
    pg.keyboard.press("Escape")
    pg.goto(BASE + "/"); pg.click("button#hk-user"); shot(pg, "menue", wait=400); pg.keyboard.press("Escape")
    pg.goto(BASE + "/"); pg.click("summary.acct-sum, details.acct > summary"); pg.click("button[data-settings=account]")
    shot(pg, "einstellungen", wait=1200)
    pg.goto(f"{BASE}/notes#{home}"); pg.wait_for_load_state("networkidle"); shot(pg, "seite", wait=1800)
    pg.goto(f"{BASE}/notes#{db}"); pg.wait_for_load_state("networkidle"); shot(pg, "datenbank", wait=1800)
    pg.locator("button.db-tab", has_text="Board").first.click(); shot(pg, "board", wait=1200)

    light = b.new_context(viewport={"width": 1440, "height": 900}, color_scheme="light", locale="de-DE", device_scale_factor=2, timezone_id="Europe/Berlin",
                          storage_state=ctx.storage_state())
    lp = light.new_page(); lp.goto(BASE + "/"); lp.wait_for_load_state("networkidle"); shot(lp, "dashboard-hell", wait=1500)

    mob = b.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark", is_mobile=True, has_touch=True, timezone_id="Europe/Berlin", locale="de-DE",
                        device_scale_factor=3, storage_state=ctx.storage_state())
    mp = mob.new_page(); mp.goto(BASE + "/"); mp.wait_for_load_state("networkidle"); shot(mp, "handy", wait=1500)

    ac = b.new_context(viewport={"width": 1440, "height": 900}, color_scheme="dark", locale="de-DE", device_scale_factor=2, timezone_id="Europe/Berlin")
    a = ac.new_page()
    a.goto(ADMIN + "/login"); a.fill("input[name=username]", "demo-admin"); a.fill("input[name=password]", PW)
    a.click("button[type=submit]"); a.wait_for_url(ADMIN + "/")
    for path, name in (("/", "admin-uebersicht"), ("/users", "admin-benutzer"), ("/services", "admin-dienste"),
                       ("/settings", "admin-allgemein"), ("/settings/oidc", "admin-sso"), ("/settings/ldap", "admin-ldap")):
        a.goto(ADMIN + path); shot(a, name, wait=500)
    a.goto(ADMIN + "/users"); a.get_by_role("link", name="demo", exact=True).click(); shot(a, "admin-benutzer-detail", wait=500)
    b.close()
