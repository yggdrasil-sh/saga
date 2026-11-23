"""Handbuch-Bilder (docs/handbuch/bilder/, Tabelle BILDER.md) per Playwright/Chromium aufnehmen - Saga 2.5.

Normal ueber tools/handbuch-stack.sh (startet die Testumgebung und ruft dieses Skript darin auf):
  tools/handbuch-stack.sh --nur a06-02,b13-07        einzelne Bilder (Name ohne Endung, auch Vorsilbe wie b18)
  tools/handbuch-stack.sh                            alle Bilder, die dieses Skript kennt
  python3 tools/screens-handbuch.py --liste          Bilder mit Welt und Art der Testumgebung zeigen

Im Lauf (Netz-Namensraum, tools/testumgebung/innen.sh) steuern es Umgebungsvariablen: HB_WELT (familie|verein), HB_ART
(server|knoten), HB_NUR (Namen), HB_OUT (Zielordner, Standard $SHOTS/handbuch), HB_FONTS (Ordner mit Zusatzschriften, z. B.
Noto Sans Runic fuer das Runensiegel). Anzeige-Hosts (saga.example, admin.saga.example, verein.example, cloud.saga.example,
bookorbit.example) zeigen per /etc/hosts des Laufs auf 127.0.1.x; tools/handbuch-fakes/fakes.py leitet Port 80 weiter.
Desktop 1440x900 Faktor 2, Handy 390x844 Faktor 3; Nummern im Bild (①②③) = Legende in BILDER.md. Nur erfundene Inhalte,
gezeichnete Bilder, Kamera als gezeichnetes Ersatzbild; keine Wiederherstellungsschluessel/-codes im Bild."""
import importlib.util
import json
import os
import re
import secrets
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "handbuch-fakes"))

SCENES = []          # (name, welt, art, fn)


def scene(name, welt, art="server"):
    def deco(fn):
        SCENES.append((name, welt, art, fn))
        return fn
    return deco


DESK = {"viewport": {"width": 1440, "height": 900}, "device_scale_factor": 2}
PHONE = {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 3, "is_mobile": True, "has_touch": True}
HOST = {"familie": "saga.example", "verein": "verein.example"}

# Nummern im Bild: Rahmen + Marke wie in den bisherigen Handbuch-Bildern (gold, hell/dunkel)
MARK_JS = r"""
([items, dark]) => {
  document.querySelectorAll('.hb-mark').forEach(e => e.remove());
  const col = dark ? '#d9ad4a' : '#7c5a10', fg = dark ? '#1b1b1b' : '#fff';
  for (const it of items) {
    const [x, y, w, h, n, pad] = it, p = pad == null ? 4 : pad;
    const r = document.createElement('div'); r.className = 'hb-mark';
    Object.assign(r.style, { position: 'fixed', left: (x - p) + 'px', top: (y - p) + 'px', width: (w + 2 * p) + 'px', height: (h + 2 * p) + 'px',
      border: '3px solid ' + col, borderRadius: '9px', zIndex: 2147483646, pointerEvents: 'none', boxSizing: 'border-box',
      boxShadow: '0 0 0 2px ' + (dark ? 'rgba(0,0,0,.35)' : 'rgba(255,255,255,.6)') });
    document.body.appendChild(r);
    if (!n) continue;
    const b = document.createElement('div'); b.className = 'hb-mark'; b.textContent = n;
    const bx = Math.max(2, x - p - 11), by = Math.max(2, y - p - 11);
    Object.assign(b.style, { position: 'fixed', left: bx + 'px', top: by + 'px', width: '24px', height: '24px', borderRadius: '50%',
      background: col, color: fg, font: '700 14px/24px system-ui, sans-serif', textAlign: 'center', zIndex: 2147483647,
      pointerEvents: 'none', boxShadow: '0 1px 3px rgba(0,0,0,.35)' });
    document.body.appendChild(b);
  }
}
"""

# Kamera/Mikrofon/Bildschirm: gezeichnete Ersatzbilder (Initialen bzw. Urlaubsfotos) und stiller Ton - WebRTC laeuft echt
MEDIA_JS = r"""
(() => {
  const who = (window.__hbWho || {});
  function canvasStream(kind) {
    const cv = document.createElement('canvas'); cv.width = 640; cv.height = 360; const g = cv.getContext('2d'); let t = 0;
    function draw() {
      t++;
      if (kind === 'screen') {
        g.fillStyle = '#f4efe4'; g.fillRect(0, 0, 640, 360);
        g.fillStyle = '#2b2b2b'; g.font = '700 22px sans-serif'; g.fillText('Fotos Ostsee 2026', 24, 38);
        const cols = [['#7fb4e6', '#f2d58a'], ['#f6d55c', '#e9a03b'], ['#9ad0a4', '#4d8f5a'], ['#f29e6c', '#d9603b'], ['#86c5da', '#3d7fa0'], ['#e8737a', '#b03a48']];
        cols.forEach((c, i) => { const x = 24 + (i % 3) * 200, y = 60 + Math.floor(i / 3) * 145;
          const gr = g.createLinearGradient(0, y, 0, y + 130); gr.addColorStop(0, c[0]); gr.addColorStop(1, c[1]); g.fillStyle = gr; g.fillRect(x, y, 184, 130);
          g.fillStyle = 'rgba(255,255,255,.75)'; g.beginPath(); g.arc(x + 140, y + 32, 16, 0, 7); g.fill(); });
      } else {
        const a = who.c1 || '#4682d2', b = who.c2 || '#2f5aa8';
        const gr = g.createLinearGradient(0, 0, 0, 360); gr.addColorStop(0, a); gr.addColorStop(1, b); g.fillStyle = gr; g.fillRect(0, 0, 640, 360);
        g.fillStyle = 'rgba(255,255,255,.16)'; g.beginPath(); g.arc(320, 420, 190 + 4 * Math.sin(t / 15), 0, 7); g.fill();
        g.beginPath(); g.arc(320, 150, 70, 0, 7); g.fill();
        g.fillStyle = '#fff'; g.font = '700 64px sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(who.ini || '?', 320, 152);
      }
    }
    draw(); setInterval(draw, 100);
    return cv.captureStream(15);
  }
  function silent() {
    const ac = new AudioContext(), o = ac.createOscillator(), gn = ac.createGain(); gn.gain.value = 0.0001;
    const d = ac.createMediaStreamDestination(); o.connect(gn); gn.connect(d); o.start(); return d.stream;
  }
  const md = navigator.mediaDevices; if (!md) return;
  md.getUserMedia = async (c) => {
    const out = new MediaStream();
    if (c && c.audio) silent().getAudioTracks().forEach(t => out.addTrack(t));
    if (c && c.video) canvasStream('cam').getVideoTracks().forEach(t => out.addTrack(t));
    return out;
  };
  md.getDisplayMedia = async () => canvasStream('screen');
  md.enumerateDevices = async () => [{ deviceId: 'default', kind: 'audioinput', label: 'Mikrofon (Standard)', groupId: 'a' },
    { deviceId: 'default', kind: 'videoinput', label: 'Kamera (Standard)', groupId: 'v' }, { deviceId: 'default', kind: 'audiooutput', label: 'Lautsprecher (Standard)', groupId: 'a' }];
})();
"""


class Run:
    """Ein Lauf: Attrappen, Demo-Welt, Browser."""

    def __init__(self, welt, art):
        self.welt, self.art = welt, art
        self.host = HOST[welt]
        self.base = "http://" + self.host
        self.adm = "http://admin." + self.host
        self.out = os.environ.get("HB_OUT") or os.path.join(os.environ.get("SHOTS", "/tmp"), "handbuch")
        os.makedirs(self.out, exist_ok=True)
        self.made = []
        self.open = []          # offene Browser-Kontexte (bei Fehler: Bild zur Fehlersuche, dann auflegen und schliessen)

    # --- Aufbau ----------------------------------------------------------------------------------------------------
    def fakes(self):
        import fakes
        P, AP = int(os.environ["PORT"]), int(os.environ["ADMIN_PORT"])
        H0 = 19001 + 10 * int(os.environ.get("SLOT", os.environ.get("HB_SLOT", "6")))
        fakes.forward("127.0.1.1", 80, P)
        fakes.forward("127.0.1.2", 80, AP)
        self.nc_login, self.nc_pw = "anna@cloud.saga.example", "app-" + secrets.token_hex(12)
        _, self.nc_ids, self.nc_uid = fakes.nextcloud_start(H0 + 5, self.nc_login, self.nc_pw)
        fakes.forward("127.0.1.3", 80, H0 + 5)
        self.bo_pw = "Buch-" + secrets.token_hex(5)
        fakes.bookorbit_start(H0 + 6, "anna", self.bo_pw, "Anna")
        fakes.forward("127.0.1.4", 80, H0 + 6)

    def demo(self):
        spec = importlib.util.spec_from_file_location("handbuch_demo", os.path.join(HERE, "handbuch-demo.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        self.dm = m.Demo(self.welt, base=self.base)
        if self.welt == "familie":
            self.ids = self.dm.familie(self.nc_login, self.nc_pw, self.nc_uid, self.nc_ids)
        else:
            self.ids = self.dm.verein()
        self.dm.save()
        print("Demo", self.welt, json.dumps(self.ids, ensure_ascii=False), flush=True)
        # Der Server haelt Einstellungen eine Weile im Speicher: warten, bis er den Titel der Demo-Welt zeigt
        import urllib.request
        t0 = time.time()
        while time.time() - t0 < 120:
            try:
                with urllib.request.urlopen(self.base + "/login", timeout=10) as r:
                    if self.dm.W["title"] in r.read().decode("utf-8", "replace"):
                        break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(2)
        print(f"Einstellungen beim Server angekommen nach {time.time() - t0:.0f} s", flush=True)

    def browser(self, pw):
        env = dict(os.environ, LANG="de_DE.UTF-8", LC_ALL="de_DE.UTF-8", TZ="Europe/Berlin")
        fonts = os.environ.get("HB_FONTS")
        if fonts and os.path.isdir(fonts):
            conf = os.path.join(fonts, "fonts.conf")
            with open(conf, "w") as fh:
                fh.write('<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
                         f'<include ignore_missing="yes">/etc/fonts/fonts.conf</include><dir>{fonts}</dir>'
                         f'<cachedir>{fonts}/cache</cachedir></fontconfig>')
            env["FONTCONFIG_FILE"] = conf
        args = ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required",
                f"--unsafely-treat-insecure-origin-as-secure={self.base},{self.adm}"]
        self.b = pw.chromium.launch(args=args, env=env, channel=os.environ.get("PW_CHANNEL") or None)

    def who(self, user, dark=False, form=DESK, media=None):
        return Who(self, user, dark, form, media)

    # --- Bilder ----------------------------------------------------------------------------------------------------
    def shot(self, page, name, el=None, full=False, wait=600, pad=0):
        page.wait_for_timeout(wait)
        path = os.path.join(self.out, name + ".png")
        if el is not None:
            bb = el.bounding_box() if hasattr(el, "bounding_box") else el
            vp = page.viewport_size
            if bb and bb["y"] + bb["height"] + pad > vp["height"] and hasattr(el, "bounding_box"):
                # hoeher als das Fenster: Fenster kurz verlaengern (Screenshot-Ausschnitt nur im sichtbaren Bereich)
                page.set_viewport_size({"width": vp["width"], "height": int(bb["y"] + bb["height"] + pad + 20)})
                page.wait_for_timeout(500)
                bb = el.bounding_box()
                clip = {"x": max(0, bb["x"] - pad), "y": max(0, bb["y"] - pad), "width": bb["width"] + 2 * pad, "height": bb["height"] + 2 * pad}
                page.screenshot(path=path, clip=clip)
                page.set_viewport_size(vp)
                self.made.append(name)
                print("BILD", name, flush=True)
                return path
            clip = {"x": max(0, bb["x"] - pad), "y": max(0, bb["y"] - pad), "width": bb["width"] + 2 * pad, "height": bb["height"] + 2 * pad}
            page.screenshot(path=path, clip=clip)
        else:
            page.screenshot(path=path, full_page=full)
        self.made.append(name)
        print("BILD", name, flush=True)
        return path


class Who:
    """Eine Person in einem eigenen Browser-Kontext (= eigenes Geraet); angemeldet per Sitzungs-Cookie."""

    def __init__(self, run, user, dark, form, media):
        self.run, self.user, self.dark = run, user, dark
        self.ctx = run.b.new_context(color_scheme="dark" if dark else "light", locale="de-DE", timezone_id="Europe/Berlin",
                                     ignore_https_errors=True, **form)
        self.ctx.grant_permissions(["camera", "microphone", "notifications"], origin=run.base)
        if media:
            self.ctx.add_init_script("window.__hbWho = " + json.dumps(media) + ";\n" + MEDIA_JS)
        self.ctx.add_init_script("if (window.PublicKeyCredential) PublicKeyCredential.isConditionalMediationAvailable = () => Promise.resolve(false);")
        if user:
            self.ctx.add_cookies([{"name": "saga", "value": run.dm.ck(user), "domain": run.host, "path": "/"}])
        self.p = self.ctx.new_page()
        self.p.on("pageerror", lambda e: print(" ", user, "pageerror", str(e)[:200], flush=True))
        run.open.append(self)

    def go(self, path, wait=1200):
        self.p.goto(self.run.base + path)
        self.p.wait_for_load_state("networkidle")
        self.p.wait_for_timeout(wait)

    def page(self, nid, wait=1800):
        self.go(f"/notes#{nid}", wait)

    def mark(self, items):
        """items: [(locator|bbox, Nummer|None[, Abstand])]"""
        rects = []
        for it in items:
            loc, n = it[0], it[1]
            pad = it[2] if len(it) > 2 else 4
            if hasattr(loc, "bounding_box") and not loc.count():
                print("  Markierung fehlt:", n, flush=True)
                continue
            bb = loc.first.bounding_box(timeout=5000) if hasattr(loc, "bounding_box") else loc
            if bb:
                rects.append([bb["x"], bb["y"], bb["width"], bb["height"], n, pad])
        self.p.evaluate(MARK_JS, [rects, self.dark])

    def unmark(self):
        self.p.evaluate("() => document.querySelectorAll('.hb-mark').forEach(e => e.remove())")

    def settings(self, key, wait=900):
        self.p.evaluate("(k) => window.HKSettings && HKSettings.open(k)", key)
        self.p.wait_for_selector(".hk-dialog-set", timeout=10000)
        self.p.wait_for_timeout(wait)
        self.blur()
        return self.p.locator(".hk-dialog-set")

    def blur(self, inputs=False):
        self.p.evaluate("(all) => { const a = document.activeElement; if (a && a !== document.body && (all || !/^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName))) a.blur(); }", inputs)

    def close(self):
        if self in self.run.open:
            self.run.open.remove(self)
        try:
            self.ctx.close()
        except Exception:  # noqa: BLE001
            pass


def admin_login(run, user, dark=False):
    w = Who(run, None, dark, DESK, None)
    w.p.goto(run.adm + "/login")
    w.p.fill("input[name=username]", user)
    w.p.fill("input[name=password]", run.dm.pw)
    w.p.click("button[type=submit]")
    w.p.wait_for_load_state("networkidle")
    return w


# =====================================================================================================
# Einstellungen (Benutzer) - Familie Sommer
# =====================================================================================================
@scene("b03-01-lesezeichen-dialog", "familie")
def b03_01(run):
    c = run.who("clara")
    c.go("/")
    dlg = c.settings("bookmarks", wait=1200)
    pane = c.p.locator(".hk-set-bookmarks")
    pane.get_by_role("button", name=re.compile(r"^\+ Lesezeichen")).first.click()
    url = pane.locator(".hk-cal-form input[type=url]").first
    url.fill("https://schule.example.org/vertretungsplan")
    url.press("End")
    c.mark([(url, "1")])
    run.shot(c.p, "b03-01-lesezeichen-dialog", el=dlg)
    c.close()


@scene("b01-04-profilbild-zuschneiden", "familie")
def b01_04(run):
    import fakes
    a = run.who("anna")
    a.go("/")
    a.settings("account")
    path = "/tmp/hb-tulpen.png"
    with open(path, "wb") as fh:
        fh.write(fakes.tulips())
    fi = a.p.locator(".hk-set-account input[type=file]").first
    fi.set_input_files(path)
    a.p.wait_for_selector(".hk-crop-stage", timeout=8000)
    rng = a.p.locator(".hk-crop .hk-range")
    rng.evaluate("(e) => { e.value = '1.35'; e.dispatchEvent(new Event('input')); }")
    a.mark([(a.p.locator(".hk-crop-stage"), "1"), (rng, "2")])
    run.shot(a.p, "b01-04-profilbild-zuschneiden")
    a.close()


@scene("b01-05-sprache", "familie")
def b01_05(run):
    a = run.who("anna")
    a.go("/")
    dlg = a.settings("lang")
    sel = a.p.locator(".hk-set-account select").first
    a.mark([(sel, "1")])
    run.shot(a.p, "b01-05-sprache", el=dlg)
    a.close()


@scene("b01-07-einstellungen-reiter", "familie")
def b01_07(run):
    a = run.who("anna", dark=True)
    a.go("/")
    a.settings("account")
    a.mark([(a.p.locator(".hk-setnav"), "1", 2)])
    run.shot(a.p, "b01-07-einstellungen-reiter")
    a.close()


@scene("b14-02-mitteilungen-einstellungen", "familie")
def b14_02(run):
    a = run.who("anna")
    a.go("/")
    dlg = a.settings("push", wait=1800)
    run.shot(a.p, "b14-02-mitteilungen-einstellungen", el=dlg)
    a.close()


@scene("b16-01-kalender-verbinden", "familie")
def b16_01(run):
    run.dm.calendar(True)              # Zugang "Familie" nur fuer dieses Bild (ohne CalDAV-Server sonst Fehler unter "Termine")
    try:
        a = run.who("anna")
        for _ in range(4):                 # Hinweis nennt die freigegebenen Hosts - nur die Anzeige-Hosts, nie 127.0.0.1:<port>
            a.go("/")
            dlg = a.settings("calendar", wait=1500)
            pane = a.p.locator(".hk-set-calendar")
            pane.get_by_role("button", name=re.compile("Kalender verbinden")).click()
            a.p.wait_for_timeout(600)
            if "127.0.0.1" not in pane.inner_text():
                break
            print("  b16-01: Server kennt noch alte allowed_hosts - neu setzen und warten", flush=True)
            run.dm.setting("allowed_hosts", "cloud.saga.example, bookorbit.example")
            time.sleep(6)
        form = pane.locator(".hk-cal-form")
        url = form.locator("input[type=url]").first
        url.fill("https://cloud.saga.example/remote.php/dav")
        form.locator("input[autocomplete=username]").first.fill("anna")
        a.blur(True)
        a.mark([(pane.locator(".hk-cal-list > *").first, "1"), (url, "2")])
        run.shot(a.p, "b16-01-kalender-verbinden", el=dlg)
        a.close()
    finally:
        run.dm.calendar(False)


@scene("b17-01-qr-kopplung", "familie")
def b17_01(run):
    a = run.who("anna")
    a.go("/")
    dlg = a.settings("apps")
    a.p.get_by_role("button", name=re.compile("QR-Code anmelden")).click()
    a.p.wait_for_selector(".hk-set-apps canvas, .hk-set-apps svg, .hk-set-apps img", timeout=8000)
    qr = a.p.locator(".hk-set-apps canvas, .hk-set-apps svg, .hk-set-apps img").first
    a.mark([(qr, "1", 8)])
    run.shot(a.p, "b17-01-qr-kopplung", el=dlg)
    a.close()


@scene("b18-01-api-token-neu", "familie")
def b18_01(run):
    a = run.who("anna")
    a.go("/")
    dlg = a.settings("api", wait=1500)
    nm = a.p.locator(".api-name").first
    nm.fill("Home Assistant")
    for s in ("read:tasks", "write:tasks", "read:calendar"):
        cb = a.p.locator(f".hk-set-api input[type=checkbox][value='{s}']")
        if cb.count():
            cb.first.check()
    scopes = a.p.locator(".hk-set-api fieldset.api-scopes").first
    a.mark([(nm, "1"), (scopes, "2")])
    run.shot(a.p, "b18-01-api-token-neu", el=dlg)
    a.close()


@scene("b18-02-webhook", "familie")
def b18_02(run):
    a = run.who("anna")
    a.go("/")
    dlg = a.settings("api", wait=1500)
    url = a.p.locator(".api-hook-url").first
    url.scroll_into_view_if_needed()
    url.fill("https://ha.saga.example/api/webhook/saga-seiten")
    btn = url.locator("xpath=following::button[1]")
    a.mark([(url, "1"), (btn, "2")])
    run.shot(a.p, "b18-02-webhook", el=dlg)
    a.close()


@scene("b19-01-diagnose-senden", "familie")
def b19_01(run):
    a = run.who("anna")
    a.go("/")
    dlg = a.settings("help")
    note = a.p.locator(".hk-diag-note").first
    note.fill("Beim Speichern von „Urlaub Ostsee 2027“ kam kurz eine Fehlermeldung.")
    btn = note.locator("xpath=following::button[1]")
    a.mark([(note, "1"), (btn, "2")])
    run.shot(a.p, "b19-01-diagnose-senden", el=dlg)
    a.close()


@scene("b19-02-mein-backup", "familie")
def b19_02(run):
    a = run.who("anna")
    a.go("/")
    dlg = a.settings("backup", wait=1500)
    run.shot(a.p, "b19-02-mein-backup", el=dlg)
    a.close()


# =====================================================================================================
# Seiten - Familie Sommer
# =====================================================================================================
@scene("b04-14-unterseiten-galerie", "familie")
def b04_14(run):
    a = run.who("anna")
    a.page(run.ids["wiki"], 2500)
    a.p.wait_for_selector('.wg[data-kind="subpages"] img', timeout=10000)
    a.p.wait_for_timeout(1200)
    run.shot(a.p, "b04-14-unterseiten-galerie")
    a.close()


@scene("b06-09-widget-reihe", "familie")
def b06_09(run):
    a = run.who("anna")
    a.page(run.ids["jahr"], 2500)
    a.p.wait_for_selector('.wg[data-kind="habit"] .hb-row', timeout=10000)
    a.p.wait_for_timeout(1500)
    cols = a.p.locator(".cols").first
    run.shot(a.p, "b06-09-widget-reihe", el=cols, pad=14)
    a.close()


@scene("b16-03-nextcloud-datei", "familie")
def b16_03(run):
    a = run.who("anna")
    a.page(run.ids["ferienwohnung"], 3000)
    run.shot(a.p, "b16-03-nextcloud-datei")
    a.close()


def bookorbit_connect(run, a):
    a.settings("bookorbit", wait=1500)
    pane = a.p.locator(".hk-set-bookorbit")
    if "Verbunden" in pane.inner_text():
        return pane
    ins = pane.locator("input")
    vals = {"url": "http://bookorbit.example", "text": "anna", "password": run.bo_pw}
    for i in range(ins.count()):
        e = ins.nth(i)
        t = (e.get_attribute("type") or "text")
        if t == "password":
            e.fill(vals["password"])
        elif t == "url" or "http" in (e.get_attribute("placeholder") or ""):
            e.fill(vals["url"])
        elif t == "text":
            e.fill(vals["text"])
    pane.get_by_role("button", name=re.compile("Anmelden|Verbinden")).first.click()
    a.p.wait_for_timeout(2500)
    return pane


@scene("b16-04-bookorbit-verbunden", "familie")
def b16_04(run):
    a = run.who("anna")
    a.go("/")
    bookorbit_connect(run, a)
    dlg = a.p.locator(".hk-dialog-set")
    a.p.wait_for_timeout(1500)
    run.shot(a.p, "b16-04-bookorbit-verbunden", el=dlg)
    a.close()


@scene("b16-05-bookorbit-buchseite", "familie")
def b16_05(run):
    a = run.who("anna")
    a.go("/")
    pane = bookorbit_connect(run, a)
    a.p.wait_for_timeout(1000)
    until(lambda: pane.get_by_text("Das Leuchtfeuer von Hiddensee").count(), 10000)
    # "Als Seite" in der Zeile des Buchs: legt die Buchseite unter "Bücher" an (Kapitel als Unterseiten) und oeffnet sie
    pane.get_by_role("button", name=re.compile("Als Seite")).first.click()
    until(lambda: "/notes" in a.p.url and a.p.locator(".hk-dialog-set").count() == 0, 20000)
    a.p.wait_for_load_state("networkidle")
    a.p.wait_for_timeout(3000)
    book = a.p.url
    # Kapitelseite mit den Markierungen als Zitate (der Seitenbaum klappt dabei bis zum Kapitel auf)
    with run.dm.db.connect() as c:                               # Kapitelseite (von Saga angelegt) aus der DB
        r = c.execute("SELECT id FROM note WHERE uid='anna' AND title=? AND deleted_at IS NULL ORDER BY id DESC",
                      ("3 Sturmflut",)).fetchone()
    a.p.goto(run.base + f"/notes#{r[0]}" if r else book)
    until(lambda: a.p.url != book, 10000)
    a.p.wait_for_load_state("networkidle")
    a.p.wait_for_timeout(2500)
    a.blur()
    run.shot(a.p, "b16-06-bookorbit-kapitel")
    a.p.goto(book)
    a.p.wait_for_load_state("networkidle")
    a.p.wait_for_timeout(2500)
    a.blur()
    run.shot(a.p, "b16-05-bookorbit-buchseite")
    a.close()


# =====================================================================================================
# Admin - Gartenverein Lindenhof
# =====================================================================================================
def admin_card(w, title):
    return w.p.locator("section.adm-card", has=w.p.locator("h3", has_text=title)).first


@scene("a05-01-allgemein-seiten", "verein")
def a05_01(run):
    w = admin_login(run, "frieda")
    w.p.goto(run.adm + "/settings")
    w.p.wait_for_load_state("networkidle")
    card = admin_card(w, "Seiten")
    card.scroll_into_view_if_needed()
    run.shot(w.p, "a05-01-allgemein-seiten", el=card, pad=8)
    w.close()


def diag_demo(run):
    """Diagnosen wie im Alltag: Paul schickt "Diagnose senden" aus dem Browser (echte Meldung), dazu Meldungen der Apps
    (dieselbe Form, Client iOS/Android) ueber dieselbe API."""
    p = run.who("paul")
    p.go("/")
    got = {}

    def grab(rq):
        if rq.url.endswith("/api/client/diag") and rq.method == "POST":
            got["body"] = rq.post_data
    p.p.on("request", grab)
    p.settings("help")
    p.p.locator(".hk-diag-note").first.fill("Frieda nicht mehr zu hören, 19:42")
    p.p.locator(".hk-diag-note").first.locator("xpath=following::button[1]").click()
    p.p.wait_for_timeout(2500)
    txt = p.p.locator(".hk-set-help").inner_text()
    m = re.search(r"D-[A-Z0-9]{5}", txt)
    run.diag_id = m.group(0) if m else None
    p.close()
    body = json.loads(got.get("body") or "{}")
    for app, ver, osn, dev, user, note in (("ios", "2.5.0", "iOS 26.1", "phone", "greta", "Kalender lädt nicht"),
                                           ("android", "2.5.0", "Android 16", "phone", "frieda", "")):
        if body:
            b = dict(body, app=app, ver=ver, os=osn, dev=dev, note=note, sid=secrets.token_hex(8))
            run.dm.api(user, "POST", "/api/client/diag", b)
    run.dm.api("paul", "POST", "/api/client/log", {"v": 1, "app": "android", "ver": "2.5.0", "os": "Android 16", "dev": "phone",
                                                   "sid": secrets.token_hex(8), "items": [
                                                       {"k": "error", "msg": "TypeError: Failed to fetch", "t": 412000}]})
    return run.diag_id


@scene("a09-02-diagnose-liste", "verein")
def a09_02(run):
    diag_demo(run)
    w = admin_login(run, "frieda")
    w.p.goto(run.adm + "/diag")
    w.p.wait_for_load_state("networkidle")
    w.p.wait_for_timeout(800)
    run.shot(w.p, "a09-02-diagnose-liste")
    w.close()


TERM_HTML = """<!doctype html><meta charset="utf-8"><style>
body{margin:0;background:#1e1f22;font:14px/1.55 'Adwaita Mono','DejaVu Sans Mono',monospace;color:#d7d7d7}
.t{padding:16px 20px;white-space:pre-wrap;word-break:break-all}.p{color:#8fcf6a}.c{color:#e5c07b}.h{color:#ffd479;font-weight:700}
.bar{background:#2b2d31;color:#aaa;padding:6px 14px;font:13px system-ui,sans-serif}</style>
<div class="bar">frieda@verein-server: ~/saga</div><div class="t">%s</div>"""


@scene("a09-01-log-ausschnitt", "verein")
def a09_01(run):
    """Terminal-Bild aus ECHTEN Log-Zeilen dieses Laufs (Format clientdiag/clientlog); Kennung auf D-7K3FM gesetzt wie im Text."""
    import html
    did = getattr(run, "diag_id", None) or diag_demo(run)
    log = open(os.environ.get("SAGA_SERVER_LOG", "/dev/null"), encoding="utf-8", errors="replace").read().splitlines()
    lines = [x for x in log if x.startswith("clientdiag ") and did and did in x][:1]
    if not lines:
        print("FEHLER a09-01: keine clientdiag-Zeile im Server-Log", flush=True)
        return
    ln = lines[0].replace(did, "D-7K3FM")
    cut = lambda x, n: html.escape(x[:n]) + ('<span class="c"> … (gekürzt)</span>' if len(x) > n else "")  # noqa: E731
    cmd = "docker compose logs --no-log-prefix saga | grep 'clientdiag {\"id\":\"D-7K3FM\"'"
    cmd2 = "docker compose logs --no-log-prefix saga | grep '^clientlog ' | tail -50"
    logs = [x for x in log if x.startswith("clientlog ")][-2:]
    body = (f'<span class="p">$</span> {html.escape(cmd)}\n' + cut(ln, 330).replace("D-7K3FM", '<span class="h">D-7K3FM</span>', 1) +
            f'\n<span class="p">$</span> {html.escape(cmd2)}\n' + "\n".join(cut(x, 330) for x in logs) + '\n<span class="p">$</span> ')
    w = Who(run, None, True, {"viewport": {"width": 1100, "height": 330}, "device_scale_factor": 2}, None)
    w.p.set_content(TERM_HTML % body)
    h = w.p.evaluate("() => document.body.scrollHeight")
    w.p.set_viewport_size({"width": 1100, "height": max(200, h)})
    run.shot(w.p, "a09-01-log-ausschnitt")
    w.close()


# =====================================================================================================
# Admin -> Anrufe (Art knoten: Server hinter saga-media mit eingebautem Knoten) - Gartenverein Lindenhof
# =====================================================================================================
def knoten_name(w):
    """Die Testumgebung nennt den eingebauten Knoten "localhost" bzw. "localhost:<port>" (Zertifikat der Test-CA) und das
    Test-Binary meldet die Version "x.y.z-probe" - im Bild stehen der Anzeige-Name der Demo und x.y.z, sonst nichts geaendert."""
    w.p.evaluate(r"""() => { const tw = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      for (let n; (n = tw.nextNode());) {
        let v = n.nodeValue;
        v = v.replace(/localhost:\d+/g, 'verein.example:443').replace(/\blocalhost\b/g, 'verein.example').replace(/(\d+\.\d+\.\d+)-probe/g, '$1');
        if (v !== n.nodeValue) n.nodeValue = v; } }""")


@scene("a06-02-knoten-liste", "verein", "knoten")
def a06_02(run):
    w = admin_login(run, "frieda")
    w.p.goto(run.adm + "/calls")
    w.p.wait_for_load_state("networkidle")
    w.p.wait_for_timeout(1500)
    knoten_name(w)
    card = w.p.locator("section#horn")
    card.scroll_into_view_if_needed()
    run.shot(w.p, "a06-02-knoten-liste", el=card, pad=8)
    w.close()


@scene("a06-04-tunnel-kasten", "verein", "knoten")
def a06_04(run):
    w = admin_login(run, "frieda")
    w.p.goto(run.adm + "/calls")
    w.p.wait_for_load_state("networkidle")
    box = w.p.locator(".horn-tunnel")
    box.scroll_into_view_if_needed()
    b = box.locator(".tu-btn")
    if b.count() and b.is_visible():
        b.click()
        until(lambda: "✓" in box.locator(".tu-check").inner_text() or "✗" in box.locator(".tu-check").inner_text(), 15000)
    w.p.wait_for_timeout(600)
    knoten_name(w)
    run.shot(w.p, "a06-04-tunnel-kasten", el=box, pad=10)
    w.close()


# =====================================================================================================
# Anrufe (Art knoten) - Familie Sommer. Kamera = gezeichnete Initialen, Bildschirm = gezeichnete Urlaubsfotos.
# =====================================================================================================
MEDIA = {"anna": {"ini": "AS", "c1": "#f08050", "c2": "#e85c70"}, "ben": {"ini": "BS", "c1": "#4682d2", "c2": "#3c5aaa"},
         "clara": {"ini": "CS", "c1": "#46aa6e", "c2": "#287a5a"}, "lena": {"ini": "OL", "c1": "#be6ec8", "c2": "#8c50aa"}}
ANDROID_UA = ("Mozilla/5.0 (Linux; Android 15; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36")


def until(fn, ms=20000, step=200):
    end = time.time() + ms / 1000
    v = None
    while time.time() < end:
        try:
            v = fn()
        except Exception:  # noqa: BLE001 - Seite laedt gerade neu
            v = None
        if v:
            return v
        time.sleep(step / 1000)
    return v


def caller(run, user, dark=True, form=DESK):
    w = run.who(user, dark=dark, form=form, media=MEDIA[user])
    keys(run, w)
    return w


def keys(run, w):
    """Chat-Schluessel einrichten (erstes Geraet) bzw. mit dem Wiederherstellungsschluessel freischalten - der Schluessel
    bleibt im Speicher dieses Laufs und erscheint in keinem Bild."""
    w.go("/chats", 2500)
    p = w.p
    rk = getattr(run, "rk", {})
    run.rk = rk
    if p.locator(".ct-setup-go").count():
        p.click(".ct-setup-go")
        p.wait_for_selector("#ct-rk")
        rk[w.user] = p.text_content("#ct-rk").strip()
        p.click(".hk-actions .btn-primary")
        p.fill(".ct-rk-last", rk[w.user][-4:])
        p.click(".ct-setup-done")
        p.wait_for_selector(".ct-setup-done", state="detached", timeout=60000)
    elif p.locator(".ct-rk-go").count() and rk.get(w.user):
        p.click(".ct-rk-go")
        p.fill(".ct-rk-in", rk[w.user])
        p.click(".ct-rk-ok")
        p.wait_for_selector(".ct-rk-ok", state="detached", timeout=60000)
    p.wait_for_timeout(800)
    until(lambda: p.evaluate("() => !!(window.HKLive && HKLive.state().sse)"), 15000)


def japi(w, m, url, body=None):
    return w.p.evaluate("([m, u, b]) => HK.api(m, u, b).then(r => r, e => ({error: e.message, status: e.status, data: e.data}))", [m, url, body])


def device(w):
    return w.p.evaluate("() => window.HKChat && HKChat.state().device")


def room(w):
    return w.p.evaluate("() => window.HKCalls && HKCalls.room()")


def call_from_chat(a, cid, video=True):
    a.go(f"/chats#c{cid}", 1500)
    a.p.wait_for_selector("#ct-chat:not([hidden])")
    sel = "#ct-call-v" if video else "#ct-call-a"
    until(lambda: a.p.locator(sel).is_visible(), 10000)
    a.p.click(sel)


def accept(w):
    w.p.wait_for_selector(".cl-ring", timeout=20000)
    w.p.click(".cl-ring .cl-big")
    return until(lambda: (room(w) or {}).get("inRoom"), 25000)


def open_list(w):
    p = w.p
    if p.locator(".cl-plist:not(.cl-addp)").count():
        return True
    if p.locator(".cl-ctl .cl-people").count():
        p.click(".cl-ctl .cl-people")
    elif p.locator(".cl-ctl .cl-more").count():
        p.click(".cl-ctl .cl-more")
        p.wait_for_selector(".cl-moremenu .cl-people", timeout=3000)
        p.click(".cl-moremenu .cl-people")
    return until(lambda: p.locator(".cl-plist:not(.cl-addp)").count(), 4000)


def no_toasts(w):
    w.p.evaluate("() => document.querySelectorAll('.toast, .hk-toast').forEach(t => t.remove())")


def hangup(*ws):
    for w in ws:
        try:
            h = w.p.locator(".cl-ctl .cl-red")
            if h.count():
                h.first.click()
        except Exception:  # noqa: BLE001
            pass


@scene("b13-08-teilnehmer-nicht-dabei", "familie", "knoten")
def b13_08(run):
    for u in ("clara", "lena"):             # einmal angemeldet (Geraet mit Chat-Schluessel), dann weg: nehmen nicht ab
        w = caller(run, u)
        w.close()
    a, b = caller(run, "anna"), caller(run, "ben")
    mk = japi(a, "POST", "/api/chats", {"kind": "free", "members": ["ben", "clara", "lena"], "device": device(a)})
    cid = mk["chat"]["id"]
    b.go("/chats", 1500)
    call_from_chat(a, cid)
    accept(b)
    open_list(a)
    # Clara und Oma Lena nehmen nicht ab: nach dem Klingeln stehen sie unter "Nicht dabei" mit "Erneut klingeln"
    until(lambda: a.p.locator(".cl-plist", has_text="Erneut klingeln").count(), 90000, 500)
    a.p.wait_for_timeout(1500)
    add = a.p.locator(".cl-plist .cl-add-go")
    away = a.p.locator(".cl-plist", has_text="Nicht dabei").locator("text=/Nicht dabei/").first
    again = a.p.locator(".cl-plist button").filter(has_text=re.compile(r"^Erneut klingeln$")).first
    no_toasts(a)
    a.mark([(away, "1", 3), (again, "2", 3), (add, "3", 3)])
    run.shot(a.p, "b13-08-teilnehmer-nicht-dabei")
    a.unmark()
    hangup(b, a)
    a.p.wait_for_timeout(1500)
    a.close()
    b.close()


@scene("b13-09-einzel-zu-gruppe", "familie", "knoten")
def b13_09(run):
    a, b, c = caller(run, "anna"), caller(run, "ben"), caller(run, "clara")
    dm = japi(a, "POST", "/api/chats", {"kind": "dm", "user": "ben"})
    cid = dm["chat"]["id"]
    b.go("/chats", 1200)
    c.go("/chats", 1200)
    call_from_chat(a, cid)
    accept(b)
    until(lambda: (room(a) or {}).get("inRoom"), 15000)
    a.p.wait_for_timeout(2000)
    open_list(a)
    a.p.click(".cl-plist .cl-add-go")
    a.p.fill(".cl-addp .cl-add-q", "Clara")
    until(lambda: a.p.locator(".cl-addp .cl-add-p", has_text="Clara Sommer").count(), 8000)
    a.p.locator(".cl-addp .cl-add-p", has_text="Clara Sommer").first.click()
    accept(c)
    until(lambda: len(set((room(a) or {}).get("legs") or [])) >= 3, 20000)
    a.p.wait_for_timeout(4000)
    if a.p.locator(".cl-addp").count():
        a.p.keyboard.press("Escape")
    if a.p.locator(".cl-plist").count():
        a.p.click(".cl-ctl .cl-people") if a.p.locator(".cl-ctl .cl-people").count() else None
    a.p.wait_for_timeout(1500)
    no_toasts(a)
    run.shot(a.p, "b13-09-einzel-zu-gruppe")
    hangup(c, b, a)
    a.p.wait_for_timeout(2500)
    # Eintrag "In Gruppenanruf verschoben" im bisherigen 1:1 (Bens Sicht, nach dem Auflegen)
    b.go(f"/chats#c{cid}", 3000)
    no_toasts(b)
    entry = b.p.locator(".ct-call-moved").first
    if entry.count():
        b.mark([(entry, "1", 3)])
    run.shot(b.p, "b13-10-in-gruppenanruf-verschoben", el={"x": 0, "y": 0, "width": 1440, "height": 322})
    for w in (a, b, c):
        w.close()


@scene("b13-07-begleitgeraet-teilt", "familie", "knoten")
def b13_07(run):
    from PIL import Image
    ph = run.who("anna", dark=True, form=dict(PHONE, user_agent=ANDROID_UA), media=MEDIA["anna"])
    keys(run, ph)
    lap = caller(run, "anna")
    b = caller(run, "ben")
    dm = japi(ph, "POST", "/api/chats", {"kind": "dm", "user": "ben"})
    cid = dm["chat"]["id"]
    b.go("/chats", 1200)
    call_from_chat(ph, cid)
    accept(b)
    until(lambda: (room(ph) or {}).get("inRoom"), 15000)
    lap.go("/chats", 2500)
    lap.p.wait_for_selector(".cl-run", timeout=20000)
    lap.p.click(".cl-run .cl-pill:not(.cl-run-go)")              # Hinzufuegen (Begleitgeraet)
    until(lambda: lap.p.locator(".cl-ctl button[aria-label='Bildschirm teilen']").count(), 25000)
    lap.p.wait_for_timeout(2500)
    lap.p.locator(".cl-ctl button[aria-label='Bildschirm teilen']").first.click()
    if until(lambda: lap.p.locator(".cl-hint .cl-hint-go").count(), 3000):
        lap.p.click(".cl-hint .cl-hint-go")
    lap.p.wait_for_timeout(6000)
    no_toasts(lap)
    no_toasts(ph)
    pa = run.shot(lap.p, "x-b13-07-laptop")
    pb = run.shot(ph.p, "x-b13-07-handy")
    # Zusammensetzen: Laptop links, Handy rechts (gleiche Hoehe), heller Grund
    L, P = Image.open(pa).convert("RGB"), Image.open(pb).convert("RGB")
    h = 1350
    L = L.resize((int(L.width * h / L.height), h), Image.LANCZOS)
    P = P.resize((int(P.width * h / P.height), h), Image.LANCZOS)
    m = 40
    im = Image.new("RGB", (L.width + P.width + 3 * m, h + 2 * m), (236, 232, 222))
    im.paste(L, (m, m))
    im.paste(P, (L.width + 2 * m, m))
    out = os.path.join(run.out, "b13-07-begleitgeraet-teilt.png")
    im.save(out, optimize=True)
    run.made.append("b13-07-begleitgeraet-teilt")
    print("BILD b13-07-begleitgeraet-teilt", flush=True)
    hangup(lap, b, ph)
    ph.p.wait_for_timeout(1500)
    for w in (ph, lap, b):
        w.close()


# =====================================================================================================
def optimize(out, names):
    """Verlustfrei verkleinern: pyoxipng (falls im PYTHONPATH, z. B. $HB_DIR/pylib), sonst Pillow optimize=True."""
    try:
        import oxipng
    except ImportError:
        oxipng = None
    from PIL import Image
    tot0 = tot1 = 0
    for n in names:
        p = os.path.join(out, n + ".png")
        if not os.path.exists(p):
            continue
        b0 = os.path.getsize(p)
        if oxipng:
            try:
                oxipng.optimize(p, level=4, strip=oxipng.StripChunks.safe())
            except TypeError:
                oxipng.optimize(p, level=4)
        else:
            Image.open(p).save(p, optimize=True)
        tot0, tot1 = tot0 + b0, tot1 + os.path.getsize(p)
    print(f"PNG verkleinert ({'oxipng' if oxipng else 'Pillow'}): {tot0 // 1024} KB -> {tot1 // 1024} KB", flush=True)


def main():
    nur = [x.strip() for x in (os.environ.get("HB_NUR") or "").replace(" ", ",").split(",") if x.strip()]
    if "--nur" in sys.argv:
        nur = [x for x in sys.argv[sys.argv.index("--nur") + 1].split(",") if x]
    want = [s for s in SCENES if not nur or any(s[0] == n or s[0].startswith(n) for n in nur)]
    if "--liste" in sys.argv or "--gruppen" in sys.argv:
        if "--liste" in sys.argv:
            for n, w, a, _ in SCENES:
                print(f"{n}\t{w}\t{a}")
        else:
            groups = {}
            for n, w, a, _ in want:
                groups.setdefault((w, a), []).append(n)
            for (w, a), ns in groups.items():
                print(w, a, ",".join(ns))
        return 0
    welt, art = os.environ.get("HB_WELT", "familie"), os.environ.get("HB_ART", "server")
    want = [s for s in want if s[1] == welt and s[2] == art]
    sys.path.insert(0, ROOT)
    os.environ["BASE_URL"] = "http://" + HOST[welt]
    from playwright.sync_api import sync_playwright
    run = Run(welt, art)
    run.fakes()
    run.demo()
    fail = []
    with sync_playwright() as pw:
        run.browser(pw)
        for n, _, _, fn in want:
            t0 = time.time()
            try:
                fn(run)
                print(f"OK  {n} ({time.time() - t0:.0f} s)", flush=True)
            except Exception:  # noqa: BLE001 - ein Bild darf die anderen nicht verhindern
                fail.append(n)
                print(f"FAIL {n}", flush=True)
                traceback.print_exc()
                for i, w in enumerate(list(run.open)):
                    try:
                        w.p.screenshot(path=os.path.join(run.out, f"x-fehler-{n}-{i}-{w.user}.png"))
                    except Exception:  # noqa: BLE001
                        pass
                    hangup(w)
                time.sleep(2)
                for w in list(run.open):
                    w.close()
        run.b.close()
    optimize(run.out, [m for m in run.made if not m.startswith("x-")])
    print("Bilder:", len(run.made), "Fehler:", ",".join(fail) or "-", "Ordner:", run.out, flush=True)
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
