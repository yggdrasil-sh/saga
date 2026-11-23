"""Handbuch-Bilder (docs/handbuch/bilder/, Tabelle BILDER.md) per Playwright/Chromium aufnehmen - Saga 2.5.

Normal ueber tools/handbuch-stack.sh (startet die Testumgebung und ruft dieses Skript darin auf):
  tools/handbuch-stack.sh --nur a06-02,b13-07        einzelne Bilder (Name ohne Endung, auch Vorsilbe wie b18)
  tools/handbuch-stack.sh                            alle Bilder, die dieses Skript kennt
  python3 tools/screens-handbuch.py --liste          Bilder mit Welt und Art der Testumgebung zeigen

Im Lauf (Netz-Namensraum, tools/testumgebung/innen.sh) steuern es Umgebungsvariablen: HB_WELT (familie|verein), HB_ART
(server|knoten), HB_NUR (Namen), HB_OUT (Zielordner, Standard $SHOTS/handbuch), HB_FONTS (Ordner mit Zusatzschriften, z. B.
Noto Sans Runic fuer das Runensiegel). Anzeige-Hosts (saga.example, admin.saga.example, verein.example, admin.verein.example,
cloud.saga.example, bookorbit.example, wetter.saga.example, ki.saga.example) zeigen per /etc/hosts des Laufs auf 127.0.1.x;
tools/handbuch-fakes/fakes.py leitet Port 80 weiter. In der Welt familie startet die Szene b11 verein.example als zweiten
Server (eigene DB, wird am Ende geloescht) fuer die Foederations-Bilder. Nicht im Skript: b01-01/b01-06 (Passkey-Knopf nur
ueber https), b04-03/b04-09, b06-06, b07-07/b07-08/b07-10/b07-11, b09-01/b09-03, b12-06, b15-02/b15-03, b16-02 (BILDER.md).
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
        main, adm = ("127.0.1.1", "127.0.1.2") if self.welt == "familie" else ("127.0.1.7", "127.0.1.8")
        fakes.forward(main, 80, P)
        fakes.forward(adm, 80, AP)
        if self.welt == "familie":                       # zweiter Server verein.example (Foederation), startet bei Bedarf
            self.p2, self.ap2 = P + 6, P + 7
            fakes.forward("127.0.1.7", 80, self.p2)
            fakes.forward("127.0.1.8", 80, self.ap2)
        self.nc_login, self.nc_pw = "anna@cloud.saga.example", "app-" + secrets.token_hex(12)
        _, self.nc_ids, self.nc_uid = fakes.nextcloud_start(H0 + 5, self.nc_login, self.nc_pw)
        fakes.forward("127.0.1.3", 80, H0 + 5)
        self.bo_pw = "Buch-" + secrets.token_hex(5)
        fakes.bookorbit_start(H0 + 6, "anna", self.bo_pw, "Anna")
        fakes.forward("127.0.1.4", 80, H0 + 6)
        fakes.weather_start(H0 + 7)                       # Wetter-Widget (Open-Meteo-Format) unter wetter.saga.example
        fakes.forward("127.0.1.5", 80, H0 + 7)
        fakes.ki_start(H0 + 8)                            # KI-Assistent (OpenAI-kompatibel) unter ki.saga.example
        fakes.forward("127.0.1.6", 80, H0 + 8)

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
                # Oberkante ins Bild holen (nach scroll_into_view_if_needed kann sie ueber dem Fenster liegen - sonst
                # fehlt oben ein Stueck, z. B. a06-01 ohne Ueberschrift)
                page.set_viewport_size({"width": vp["width"], "height": int(max(bb["y"], 0) + bb["height"] + 2 * pad + 40)})
                page.wait_for_timeout(300)
                el.evaluate("(e, p) => { e.scrollIntoView({block: 'start'}); window.scrollBy(0, -p); }", pad)
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
            run.dm.setting("allowed_hosts", "cloud.saga.example, bookorbit.example, wetter.saga.example")
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
# Heute, Seiten, Datenbanken, Teilen - Familie Sommer (Shell mit Seitenleiste links, Benutzermenue unten links)
# =====================================================================================================
def menu_item(w, text):
    return w.p.locator("button.menu-item", has_text=text).first


def visit(w, *keys):
    """Seiten oeffnen (fuer "Weitermachen" auf Heute und "Zuletzt")."""
    for k in keys:
        w.page(w.run.ids[k], 1500)


def heute(run, user="anna", dark=False, form=DESK):
    w = run.who(user, dark=dark, form=form)
    visit(w, "einkauf", "sommerfest", "urlaub")
    w.go("/dashboard", 2500)
    w.blur()
    return w


@scene("b02-01-dashboard", "familie")
def b02_01(run):
    a = heute(run)
    a.mark([(a.p.locator("#pg-mainnav"), "1", 3), (a.p.locator(".hz-acts"), "2", 4), (a.p.locator(".cats"), "3", 4),
            (a.p.locator("#rail-hide"), "4", 3)])
    run.shot(a.p, "b02-01-dashboard")
    a.close()


@scene("b02-02-dashboard-dunkel", "familie")
def b02_02(run):
    a = heute(run, dark=True)
    run.shot(a.p, "b02-02-dashboard-dunkel")
    a.close()


@scene("b02-03-dashboard-handy", "familie")
def b02_03(run):
    a = heute(run, form=PHONE)
    run.shot(a.p, "b02-03-dashboard-handy")
    a.close()


@scene("b02-04-startseite-festlegen", "familie")
def b02_04(run):
    a = run.who("anna")
    a.page(run.ids["sommerfest"], 2000)
    a.p.click("#pg-more")
    a.p.wait_for_timeout(700)
    it = menu_item(a, "Als Startseite festlegen")
    a.mark([(it, "1", 2)])
    menu = it.locator("xpath=ancestor::*[contains(@class,'menu')][1]")
    run.shot(a.p, "b02-04-startseite-festlegen", el=menu, pad=24)
    a.close()


@scene("b03-02-kacheln", "familie")
def b03_02(run):
    c = run.who("clara")
    c.go("/dashboard", 2500)
    c.blur()
    c.mark([(c.p.locator(".tw.own").last, "1", 3), (c.p.locator("#bm-add"), "2", 3)])
    run.shot(c.p, "b03-02-kacheln")
    c.close()


@scene("b04-01-seitenbaum", "familie")
def b04_01(run):
    a = run.who("anna")
    a.page(run.ids["urlaub"], 2500)
    side = a.p.locator("#pg-side")
    a.mark([(a.p.locator("#pg-search"), "1", 2), (a.p.locator("#pg-new"), "2", 2), (a.p.locator("#pg-shared-box"), "3", 2),
            (a.p.locator("#pg-qs-btn"), "4", 2), (a.p.locator("#pg-archive-btn"), "5", 2), (a.p.locator("#pg-trash-btn"), "6", 2)])
    run.shot(a.p, "b04-01-seitenbaum", el=side, pad=0)
    a.close()


@scene("b04-10-befehlspalette", "familie")
def b04_10(run):
    a = run.who("anna", dark=True)
    a.page(run.ids["urlaub"], 2500)
    a.p.keyboard.press("Control+k")
    a.p.wait_for_timeout(800)
    a.p.keyboard.type("Re", delay=80)
    a.p.wait_for_timeout(1500)
    run.shot(a.p, "b04-10-befehlspalette")
    a.close()


@scene("b04-11-editor-handy", "familie")
def b04_11(run):
    a = run.who("anna", form=PHONE)
    a.page(run.ids["urlaub"], 3000)
    run.shot(a.p, "b04-11-editor-handy")
    a.close()


@scene("b04-12-versionen", "familie")
def b04_12(run):
    a = run.who("anna")
    a.page(run.ids["urlaub"], 2500)
    a.p.click("#pg-more")
    a.p.wait_for_timeout(500)
    menu_item(a, "Versionsverlauf").click()
    a.p.wait_for_timeout(2000)
    run.shot(a.p, "b04-12-versionen")
    a.close()


@scene("b05-03-gestaltet-dunkel", "familie")
def b05_03(run):
    c = run.who("clara", dark=True)
    c.page(run.ids["lernplan"], 3000)
    run.shot(c.p, "b05-03-gestaltet-dunkel")
    c.close()


def db_page(run, key, user="anna", dark=False, form=DESK, view=None):
    w = run.who(user, dark=dark, form=form)
    w.page(run.ids[key], 2500)
    if view:
        tab = w.p.locator(".db-tabs .db-tab", has_text=view).first
        if "on" not in (tab.get_attribute("class") or "").split():       # Klick auf den aktiven Reiter oeffnet dessen Menue
            tab.click()
            w.p.wait_for_timeout(1800)
    w.blur()
    return w


@scene("b07-01-tabelle-einkauf", "familie")
def b07_01(run):
    a = db_page(run, "einkauf")
    a.mark([(a.p.locator(".db-tabs"), "1", 3), (a.p.locator(".db-chip.db-filter").first, "2", 3),
            (a.p.locator(".db-chip.db-gear").first, "3", 3), (a.p.locator(".db-new-top").first, "4", 3)])
    run.shot(a.p, "b07-01-tabelle-einkauf")
    a.close()


@scene("b07-02-board-status", "familie")
def b07_02(run):
    a = db_page(run, "hausputz", view="Board")
    run.shot(a.p, "b07-02-board-status")
    a.close()


@scene("b07-03-kalender-woche", "familie")
def b07_03(run):
    a = db_page(run, "kalender", view="Kalender")
    run.shot(a.p, "b07-03-kalender-woche")
    a.close()


@scene("b07-04-galerie-rezepte", "familie")
def b07_04(run):
    a = db_page(run, "rezepte", view="Galerie")
    run.shot(a.p, "b07-04-galerie-rezepte")
    a.close()


@scene("b07-05-zeitleiste-umzug", "familie")
def b07_05(run):
    a = db_page(run, "kalender", view="Zeitleiste")
    run.shot(a.p, "b07-05-zeitleiste-umzug")
    a.close()


@scene("b07-06-diagramm", "familie")
def b07_06(run):
    a = db_page(run, "hausputz", dark=True, view="Aufwand je Raum")
    run.shot(a.p, "b07-06-diagramm")
    a.close()


@scene("b07-09-peek", "familie")
def b07_09(run):
    a = db_page(run, "rezepte", view="Galerie")
    a.p.locator(".dbg-card", has_text="Apfelkuchen").first.click()
    a.p.wait_for_timeout(2500)
    a.mark([(a.p.locator("#peek-mode"), 1), (a.p.locator("#peek-full"), 2)])      # Text B07 Abschnitt 7: (1) rechts/mittig, (2) als Seite
    run.shot(a.p, "b07-09-peek")
    a.close()


@scene("b07-13-tabelle-handy", "familie")
def b07_13(run):
    a = db_page(run, "einkauf", form=PHONE)
    run.shot(a.p, "b07-13-tabelle-handy")
    a.close()


@scene("b10-02-kommentare", "familie")
def b10_02(run):
    a = run.who("anna")
    a.page(run.ids["urlaub"], 2500)
    btn = a.p.locator("#pg-cm-btn")
    btn.click()
    a.p.wait_for_timeout(1800)
    a.mark([(btn, "1", 3)])
    run.shot(a.p, "b10-02-kommentare")
    a.close()


@scene("b10-05-mit-mir-geteilt", "familie")
def b10_05(run):
    w = run.who("lena")
    w.go("/notes", 2500)
    w.mark([(w.p.locator("#pg-shared-box"), "1", 3)])
    run.shot(w.p, "b10-05-mit-mir-geteilt")
    w.close()


def urlaub_block(run, sel, dark=False):
    a = run.who("anna", dark=dark)
    a.page(run.ids["urlaub"], 2500)
    el = a.p.locator(sel).first
    el.scroll_into_view_if_needed()
    a.p.wait_for_timeout(600)
    return a, el


@scene("b04-05-spalten-ziehen", "familie")
def b04_05(run):
    a, el = urlaub_block(run, ".pm-b-columns")
    el.hover()
    a.p.wait_for_timeout(800)
    a.mark([(el.locator(".pm-col-rs, .col-rs").first, "1", 2), (el.locator(".pm-cols-ctl").first, "2", 2)])
    bb = el.bounding_box()
    run.shot(a.p, "b04-05-spalten-ziehen", el={"x": bb["x"] - 40, "y": bb["y"] - 60, "width": bb["width"] + 80, "height": bb["height"] + 90})
    a.close()


@scene("b04-06-tabelle-farben", "familie")
def b04_06(run):
    a, el = urlaub_block(run, ".pm-doc table")
    el.locator("td, th").nth(4).click()
    a.p.wait_for_timeout(900)
    bar = a.p.get_by_role("button", name=re.compile(r"^Zeile")).first.locator("xpath=..")
    a.mark([(bar, "1", 2)])
    bb = el.bounding_box()
    run.shot(a.p, "b04-06-tabelle-farben", el={"x": bb["x"] - 40, "y": bb["y"] - 70, "width": bb["width"] + 80, "height": bb["height"] + 110})
    a.close()


@scene("b04-07-mathe-mermaid", "familie")
def b04_07(run):
    a, el = urlaub_block(run, ".hk-mathblk, .pm-doc .katex-display")
    mer = a.p.locator(".hk-mermaid, .hk-code").first
    mer.scroll_into_view_if_needed()
    a.p.wait_for_timeout(1500)
    mer.hover()
    a.p.wait_for_timeout(500)
    b1, b2 = el.bounding_box(), mer.bounding_box()
    y0, y1 = min(b1["y"], b2["y"]) - 20, max(b1["y"] + b1["height"], b2["y"] + b2["height"]) + 20
    a.mark([(mer.get_by_role("button", name=re.compile("Vorschau")).first, "1", 2)])
    run.shot(a.p, "b04-07-mathe-mermaid", el={"x": b2["x"] - 30, "y": y0, "width": b2["width"] + 60, "height": y1 - y0})
    a.close()


@scene("b04-04-block-menu", "familie")
def b04_04(run):
    a, el = urlaub_block(run, ".pm-doc .pm-callout", dark=True)
    el.hover()
    a.p.wait_for_timeout(600)
    h = a.p.locator(".pm-handles.pm-handles-on .pm-handle-menu").first
    a.mark([(h, "1", 2)])
    h.click()
    a.p.wait_for_timeout(800)
    mn = a.p.locator(".menu:visible").last
    bb, mb = el.bounding_box(), (mn.bounding_box() if mn.count() else None) or el.bounding_box()
    y0 = max(0, min(bb["y"], mb["y"]) - 24)
    run.shot(a.p, "b04-04-block-menu", el={"x": max(0, min(bb["x"], mb["x"]) - 60), "y": y0, "width": 900,
                                          "height": max(bb["y"] + bb["height"], mb["y"] + mb["height"]) + 24 - y0})
    a.close()


@scene("b05-01-titelbild-symbol", "familie")
def b05_01(run):
    a = run.who("anna")
    a.page(run.ids["rezepte"], 2500)
    cv = a.p.locator("#pg-cover, .pg-cover").first
    cv.hover()
    a.p.wait_for_timeout(700)
    a.mark([(a.p.locator("#pg-cover-change").locator("xpath=.."), "1", 3), (a.p.locator("#pg-icon"), "2", 3)])
    run.shot(a.p, "b05-01-titelbild-symbol", el={"x": 376, "y": 0, "width": 1064, "height": 520})
    a.close()


def gallery(run, preview=None):
    a = run.who("anna")
    a.go("/notes?gallery=1", 2500)
    a.p.wait_for_selector(".gal-grid", timeout=10000)
    a.p.wait_for_timeout(1200)
    if preview:
        a.p.locator(".gal-grid > *", has_text=preview).first.click()
        a.p.wait_for_timeout(1500)
    a.blur()
    return a


@scene("b06-01-galerie", "familie")
def b06_01(run):
    a = gallery(run)
    run.shot(a.p, "b06-01-galerie")
    a.close()


@scene("b06-02-galerie-vorschau", "familie")
def b06_02(run):
    a = gallery(run, "Home-Dashboard")
    run.shot(a.p, "b06-02-galerie-vorschau")
    a.close()


@scene("b06-03-wetter-ort", "familie")
def b06_03(run):
    a = gallery(run, "Home-Dashboard")
    a.p.get_by_role("button", name=re.compile("^Verwenden")).first.click()
    a.p.wait_for_selector(".gal-wx-q", timeout=8000)
    q = a.p.locator(".gal-wx-q")
    q.fill("Lübeck")
    a.p.locator(".gal-wx button", has_text="Suchen").first.click()
    a.p.wait_for_selector(".gal-wx-hits .menu-item", timeout=8000)
    a.p.wait_for_timeout(600)
    a.mark([(q, "1", 3)])
    dlg = a.p.locator("#pg-modal > *").first
    run.shot(a.p, "b06-03-wetter-ort", el=dlg)
    a.close()


def home(run, dark=False, form=DESK):
    a = run.who("anna", dark=dark, form=form)
    a.page(run.ids["home"], 3500)
    a.blur()
    return a


@scene("b06-04-home-dashboard", "familie")
def b06_04(run):
    a = home(run)
    run.shot(a.p, "b06-04-home-dashboard", full=True)
    a.close()


@scene("b06-05-home-dashboard-dunkel", "familie")
def b06_05(run):
    a = home(run, dark=True)
    run.shot(a.p, "b06-05-home-dashboard-dunkel")
    a.close()


@scene("b06-07-home-dashboard-handy", "familie")
def b06_07(run):
    a = home(run, form=PHONE)
    run.shot(a.p, "b06-07-home-dashboard-handy")
    a.close()


@scene("b08-01-aufgabenleiste", "familie")
def b08_01(run):
    a = run.who("anna")
    a.go("/dashboard", 2500)
    a.blur()
    a.mark([(a.p.locator("#rail-add"), "1", 3), (a.p.locator("#rail-list"), "2", 3), (a.p.locator("#rail-all"), "3", 3)])
    run.shot(a.p, "b08-01-aufgabenleiste", el=a.p.locator("#rail"), pad=0)
    a.close()


@scene("b08-02-aufgabe-optionen", "familie")
def b08_02(run):
    a = run.who("anna", form=PHONE)
    a.go("/dashboard", 2500)
    a.p.locator("#nt > summary").click()
    a.p.wait_for_timeout(600)
    inp = a.p.locator("#task-text")
    inp.fill("Fahrrad aufpumpen")
    prio, rep, due = a.p.locator("#task-prio-btn"), a.p.locator("#task-rep-btn"), a.p.locator("#task-due-btn")
    rep.click()
    a.p.wait_for_timeout(700)
    a.mark([(prio, "1", 2), (rep, "2", 2), (due, "3", 2)])
    run.shot(a.p, "b08-02-aufgabe-optionen")
    a.close()


@scene("b08-06-rueckgaengig", "familie")
def b08_06(run):
    a = run.who("anna", dark=True, form=PHONE)
    a.go("/dashboard", 2500)
    a.p.locator("#nt > summary").click()
    a.p.wait_for_timeout(600)
    box = a.p.locator("#task-list li", has_text="Blumen gießen").first.locator("button.task-check").first
    box.click()
    a.p.wait_for_timeout(600)
    undo = a.p.locator(".toast-action", has_text="Rückgängig").first
    a.mark([(undo, "1", 3)])
    run.shot(a.p, "b08-06-rueckgaengig")
    a.close()


@scene("b08-03-aufgabenquelle", "familie")
def b08_03(run):
    a = db_page(run, "hausputz", view="Tabelle")
    a.p.locator(".db-chip.db-gear").first.click()
    a.p.wait_for_timeout(600)
    a.p.locator("button.vs-tasksrc").first.click()
    a.p.wait_for_timeout(1200)
    dlg = a.p.locator("#pg-modal > *").first
    run.shot(a.p, "b08-03-aufgabenquelle", el=dlg)
    a.close()


@scene("b09-02-versionen", "familie")
def b09_02(run):
    a = run.who("anna")
    a.page(run.ids["urlaub"], 2500)
    a.p.click("#pg-more")
    a.p.wait_for_timeout(500)
    menu_item(a, "Versionsverlauf").click()
    a.p.wait_for_timeout(1500)
    a.p.locator("#pg-modal li, #pg-modal .ver-item, #pg-modal button", has_text="Ben Sommer").first.click()
    a.p.wait_for_timeout(1500)
    run.shot(a.p, "b09-02-versionen")
    a.close()


@scene("b10-01-teilen-dialog", "familie")
def b10_01(run):
    a = run.who("anna")
    a.page(run.ids["urlaub"], 2500)
    a.p.click("#pg-share")
    a.p.wait_for_timeout(1500)
    a.blur(True)
    dlg = a.p.locator("#pg-modal > *").first
    run.shot(a.p, "b10-01-teilen-dialog", el=dlg)
    a.close()


@scene("b10-03-veroeffentlichen", "familie")
def b10_03(run):
    a = run.who("anna")
    a.page(run.ids["einladung"], 2500)
    a.p.locator("button.pg-pub-b").first.click()           # veroeffentlichte Seite: Knopf "🌐 Öffentlich" oben
    a.p.wait_for_timeout(1500)
    a.blur(True)
    dlg = a.p.locator("#pg-modal > *").first
    run.shot(a.p, "b10-03-veroeffentlichen", el=dlg)
    a.close()


@scene("b10-04-oeffentliche-seite-handy", "familie")
def b10_04(run):
    w = run.who(None, dark=True, form=PHONE)
    w.go("/@anna-sommer/sommerfest", 2500)
    run.shot(w.p, "b10-04-oeffentliche-seite-handy")
    w.close()


@scene("b05-02-seitenmenue-gestalten", "familie")
def b05_02(run):
    a = run.who("anna")
    a.page(run.ids["sommerfest"], 2500)
    a.p.click("#pg-more")
    a.p.wait_for_timeout(500)
    menu_item(a, "Darstellung").click()
    a.p.wait_for_timeout(700)
    a.mark([(menu_item(a, "Volle Breite"), "1", 2), (menu_item(a, "Schrift"), "2", 2), (menu_item(a, "Titelbild"), "3", 2)])
    run.shot(a.p, "b05-02-seitenmenue-gestalten", el={"x": 760, "y": 0, "width": 680, "height": 620})
    a.close()


@scene("b04-02-slash-menu", "familie")
def b04_02(run):
    a = run.who("anna")
    a.page(run.ids["elternabend"], 2500)
    a.p.locator(".pm-doc p").last.click()
    a.p.keyboard.type("/")
    a.p.wait_for_selector(".pm-slash", timeout=8000)
    a.p.wait_for_timeout(700)
    run.shot(a.p, "b04-02-slash-menu", el={"x": 300, "y": 0, "width": 900, "height": 900})
    a.p.keyboard.press("Escape")
    a.p.keyboard.press("Backspace")
    a.p.wait_for_timeout(2500)                        # "/" wieder weg (gespeichert), sonst steht er in b04-08
    a.close()


@scene("b04-08-erwaehnung", "familie")
def b04_08(run):
    a = run.who("anna")
    a.page(run.ids["elternabend"], 2500)
    a.p.locator(".pm-doc p").last.click()
    a.p.keyboard.type("Fragen an @", delay=60)
    a.p.wait_for_timeout(1500)
    run.shot(a.p, "b04-08-erwaehnung", el={"x": 300, "y": 120, "width": 900, "height": 700})
    a.close()


@scene("b15-01-ki-seite", "familie")
def b15_01(run):
    a = run.who("anna")
    a.page(run.ids["sommerfest"], 2500)
    until(lambda: a.p.evaluate("() => !!(window.HKAI && HKAI.open)"), 8000)
    a.p.evaluate("() => HKAI.open('ask', 'Was fehlt noch fürs Sommerfest?')")
    a.p.wait_for_timeout(800)
    a.p.locator(".ai-ta").first.press("Enter")             # Enter schickt die Frage (Knopf erst nach Tippen aktiv)
    until(lambda: a.p.locator(".ai-srcs, .ai-src").count() and not a.p.locator(".ai-stop:visible").count(), 30000)
    a.p.wait_for_timeout(1200)
    a.blur(True)
    a.mark([(a.p.locator(".ai-tab").first.locator("xpath=.."), "1", 2), (a.p.locator(".ai-cite").first, "2", 2),
            (a.p.locator(".ai-srcs").first, "3", 2)])
    run.shot(a.p, "b15-01-ki-seite")
    a.close()


@scene("b09-01-live-cursor", "familie")
def b09_01(run):
    """Anna und Ben gleichzeitig in "Sommerfest" (Gleichzeitig bearbeiten live fuer dieses Bild an): Ben markiert ein Wort."""
    notes = dict(run.dm.auth.settings().get("notes") or {}, live=True)
    run.dm.setting("notes", notes)
    time.sleep(6)
    try:
        a, b = run.who("anna"), run.who("ben")
        a.page(run.ids["sommerfest"], 3000)
        b.page(run.ids["sommerfest"], 3000)
        p = b.p.locator(".pm-doc p", has_text="Grillgut").first
        p.locator("xpath=.").evaluate("""(el) => { const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT); let n;
          while ((n = w.nextNode())) { const i = n.nodeValue.indexOf('Grillgut'); if (i >= 0) { const r = document.createRange();
            r.setStart(n, i); r.setEnd(n, i + 8); const s = getSelection(); s.removeAllRanges(); s.addRange(r); return; } } }""")
        b.p.wait_for_timeout(2500)
        a.p.wait_for_timeout(1500)
        a.mark([(a.p.locator("#pg-presence"), "1", 3)])
        run.shot(a.p, "b09-01-live-cursor")
        a.close()
        b.close()
    finally:
        run.dm.setting("notes", dict(notes, live=False))


def bell(run, user="anna", dark=False, form=DESK):
    w = run.who(user, dark=dark, form=form)
    w.go("/dashboard", 2500)
    b = w.p.locator("#hk-bell")
    b.click()
    w.p.wait_for_timeout(1500)
    return w, b


@scene("b14-01-glocke", "familie")
def b14_01(run):
    a, b = bell(run)
    a.mark([(b, "1", 3), (a.p.get_by_role("button", name=re.compile("Alle gelesen")).first, "2", 3)])
    run.shot(a.p, "b14-01-glocke")
    a.close()


@scene("b14-03-glocke-handy", "familie")
def b14_03(run):
    a, _ = bell(run, dark=True, form=PHONE)
    run.shot(a.p, "b14-03-glocke-handy")
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


def adm_go(run, user="frieda", path="/", dark=False, wait=700):
    w = admin_login(run, user, dark)
    if path != "/":
        w.p.goto(run.adm + path)
    w.p.wait_for_load_state("networkidle")
    w.p.wait_for_timeout(wait)
    return w


def adm_flash(w, text, cls=""):
    """Hinweis oben im Admin-Bereich nachstellen (gleiches Element p.adm-flash wie der Server; lokal nicht ausloesbar)."""
    w.p.evaluate("""([t, c]) => { const m = document.querySelector('main.adm-main'); const p = document.createElement('p');
      p.className = 'adm-flash ' + c; p.setAttribute('role', 'status'); p.innerHTML = t; m.insertBefore(p, m.firstChild); }""", [text, cls])
    return w.p.locator("p.adm-flash").first


@scene("a01-01-uebersicht", "verein")
def a01_01(run):
    w = adm_go(run)
    w.mark([(w.p.locator(".adm-nav"), "1", 3), (w.p.locator(".adm-stats"), "2", 6)])
    run.shot(w.p, "a01-01-uebersicht")
    w.close()


@scene("a01-02-admin-login-2fa", "verein")
def a01_02(run):
    w = Who(run, None, False, DESK, None)
    w.p.goto(run.adm + "/login")
    w.p.wait_for_load_state("networkidle")
    w.p.fill("input[name=username]", "frieda")
    w.blur(True)
    run.shot(w.p, "a01-02-admin-login-2fa")
    w.close()


@scene("a01-03-uebersicht-dunkel", "verein")
def a01_03(run):
    w = adm_go(run, dark=True)
    run.shot(w.p, "a01-03-uebersicht-dunkel")
    w.close()


@scene("a02-01-benutzerliste", "verein")
def a02_01(run):
    w = adm_go(run, path="/users")
    w.mark([(w.p.locator("table.adm-table").first, "1", 4)])
    run.shot(w.p, "a02-01-benutzerliste")
    w.close()


@scene("a02-02-benutzer-detail", "verein")
def a02_02(run):
    w = adm_go(run, path="/users/%d" % run.dm.auth.get_user("paul")["id"])
    card = lambda t: w.p.locator("section.adm-card", has=w.p.locator("h3", has_text=t)).first  # noqa: E731
    w.mark([(card("Profil & Rechte"), "1", 2), (card("Passwort setzen"), "2", 2), (card("Zweiter Faktor"), "3", 2)])
    run.shot(w.p, "a02-02-benutzer-detail")
    w.close()


@scene("a02-03-gruppen", "verein")
def a02_03(run):
    w = adm_go(run, path="/groups")
    run.shot(w.p, "a02-03-gruppen")
    w.close()


@scene("a03-01-dienste", "verein")
def a03_01(run):
    w = adm_go(run, path="/services")
    run.shot(w.p, "a03-01-dienste")
    w.close()


@scene("a03-02-dienst-formular", "verein")
def a03_02(run):
    with run.dm.db.connect() as c:
        sid = c.execute("SELECT id FROM service WHERE slug='wiki'").fetchone()[0]
    w = adm_go(run, path=f"/services/{sid}")
    run.shot(w.p, "a03-02-dienst-formular")
    w.close()


@scene("a04-01-2fa-verlangen", "verein")
def a04_01(run):
    w = adm_go(run, path="/settings")
    card = admin_card(w, "Zweiter Faktor")
    card.scroll_into_view_if_needed()
    w.mark([(card.locator("select").first, "1", 3)])
    run.shot(w.p, "a04-01-2fa-verlangen", el=card, pad=8)
    w.close()


@scene("a04-02-ldap", "verein")
def a04_02(run):
    w = adm_go(run, path="/settings/ldap")
    run.shot(w.p, "a04-02-ldap")
    w.close()


@scene("a04-03-sso", "verein")
def a04_03(run):
    w = adm_go(run, path="/settings/oidc")
    run.shot(w.p, "a04-03-sso")
    w.close()


@scene("a04-04-hinweis-proxy", "verein")
def a04_04(run):
    w = adm_go(run)
    f = adm_flash(w, "Hinweis: Anfragen kommen über einen Reverse Proxy (172.18.0.1), der nicht in TRUSTED_PROXIES steht. Dann teilen "
                     "sich alle Besucher eine Adresse (Sperre nach Fehlversuchen gilt für alle). Bitte TRUSTED_PROXIES auf diese Adresse setzen.", "err")
    w.mark([(f, "1", 3)])
    run.shot(w.p, "a04-04-hinweis-proxy", el={"x": 0, "y": 0, "width": 1440, "height": 520})
    w.close()


def adm_card_shot(run, name, title, path="/settings"):
    w = adm_go(run, path=path)
    card = admin_card(w, title)
    card.scroll_into_view_if_needed()
    run.shot(w.p, name, el=card, pad=8)
    w.close()


@scene("a05-02-allgemein-chats", "verein")
def a05_02(run):
    adm_card_shot(run, "a05-02-allgemein-chats", "Chats")


@scene("a05-03-api", "verein")
def a05_03(run):
    adm_card_shot(run, "a05-03-api", "API, Webhooks und MCP")


@scene("a05-04-ki", "verein")
def a05_04(run):
    w = adm_go(run, path="/settings/ai")
    run.shot(w.p, "a05-04-ki")
    w.close()


@scene("a05-05-mitteilungen", "verein")
def a05_05(run):
    w = adm_go(run, path="/settings/push")
    run.shot(w.p, "a05-05-mitteilungen")
    w.close()


@scene("a05-06-diagnose", "verein")
def a05_06(run):
    adm_card_shot(run, "a05-06-diagnose", "Diagnose")


@scene("a07-02-einladung", "verein")
def a07_02(run):
    run.dm.setting("fed_enabled", True)
    time.sleep(6)                                   # Einstellungen haelt der Server 5 s im Speicher
    w = adm_go(run, path="/federation")
    card = admin_card(w, "Einladen")
    card.scroll_into_view_if_needed()
    card.locator("button").filter(has_text=re.compile("Einladungscode")).first.click()
    w.p.wait_for_load_state("networkidle")
    w.p.wait_for_timeout(800)
    card = admin_card(w, "Einladen")
    card.scroll_into_view_if_needed()
    # Code ersetzen (gilt sonst einmal fuer diese Instanz): erfundener Code wie im Text
    w.p.evaluate("() => document.querySelectorAll('.fed-code code.fed-fp').forEach(e => { e.textContent = 'DEMO-EINLADUNG-4M8Q-T2ZW-K7HD'; })")
    run.shot(w.p, "a07-02-einladung", el=card, pad=8)
    w.close()


@scene("a08-01-veroeffentlicht", "familie")
def a08_01(run):
    w = adm_go(run, user="anna", path="/published")
    w.mark([(w.p.locator("table.adm-table button, table.adm-table [type=submit]").first, "1", 3)])
    run.shot(w.p, "a08-01-veroeffentlicht")
    w.close()


@scene("a10-01-update-hinweis", "verein")
def a10_01(run):
    w = adm_go(run)
    f = adm_flash(w, '🎉 Saga 2.5.1 ist erschienen (installiert: 2.5.0) – <a href="#">Was ist neu?</a> · Aktualisieren: '
                     '<code>docker compose pull &amp;&amp; docker compose up -d</code>')
    w.mark([(f, "1", 3)])
    run.shot(w.p, "a10-01-update-hinweis", el={"x": 0, "y": 0, "width": 1440, "height": 520})
    w.close()


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
@scene("a06-01-anrufe", "verein", "knoten")
def a06_01(run):
    adm_card_shot(run, "a06-01-anrufe", "1:1-Anrufe", path="/calls")


@scene("a06-03-knoten-koppeln", "verein", "knoten")
def a06_03(run):
    w = adm_go(run, path="/calls", wait=1500)
    card = w.p.locator("section#horn")
    btn = card.locator("details.horn-ext > summary").first
    btn.scroll_into_view_if_needed()
    btn.click()
    w.p.wait_for_timeout(600)
    f = w.p.locator("form.horn-pair-f")
    nm = f.locator("input[name=name]").first          # Vorschlag des Servers (naechster freier Name)
    w.blur(True)
    go = f.locator("button[type=submit]").first
    w.mark([(nm, "1", 3), (go, "2", 3)])
    run.shot(w.p, "a06-03-knoten-koppeln", el=card.locator("details.horn-ext"), pad=16)
    w.close()


@scene("a06-02-knoten-liste", "verein", "knoten")
def a06_02(run):
    w = admin_login(run, "frieda")
    w.p.goto(run.adm + "/calls")
    w.p.wait_for_load_state("networkidle")
    w.p.wait_for_timeout(1500)
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
    run.shot(w.p, "a06-04-tunnel-kasten", el=box, pad=10)
    w.close()


# =====================================================================================================
# Foederation - zweiter Server "Gartenverein Lindenhof" (verein.example) im selben Namensraum, eigene DB, FED_ALLOW_HTTP
# =====================================================================================================
def verein_server(run):
    """Startet verein.example (einmal je Lauf), legt die Verein-Demo an (gleiches Passwort) und verbindet beide Instanzen
    ueber die Admin-Oberflaechen (Einladungscode von verein.example, Fingerprint pruefen auf saga.example)."""
    if getattr(run, "verein", None):
        return run.verein
    import subprocess
    import psycopg
    import urllib.request
    dbn = "saga_h_verein_" + secrets.token_hex(3)
    base_url = os.environ["DATABASE_URL"]
    with psycopg.connect(base_url, autocommit=True) as c:
        c.execute(f"CREATE DATABASE {dbn}")
    run.verein_db = dbn
    st = os.path.join(os.environ.get("STATE_DIR", "/tmp"), "verein")
    os.makedirs(st, exist_ok=True)
    env = dict(os.environ, DATABASE_URL=re.sub(r"^(postgres(?:ql)?://[^/]*/)[^?]*", lambda m: m.group(1) + dbn, base_url),
               PORT=str(run.p2), ADMIN_PORT=str(run.ap2), BASE_URL="http://verein.example", ADMIN_URL="http://admin.verein.example",
               STATE_DIR=st, HB_BASE="http://verein.example", HB_PW=run.dm.pw, FED_ALLOW_HTTP="1", CACHE_TTL="3")
    log = open(os.path.join(st, "server.log"), "a")
    proc = subprocess.Popen([sys.executable, "run.py"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    run.procs = getattr(run, "procs", []) + [proc]
    for _ in range(120):
        try:
            urllib.request.urlopen("http://verein.example/healthz", timeout=2)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    r = subprocess.run([sys.executable, os.path.join(HERE, "handbuch-demo.py"), "verein"], cwd=ROOT, env=dict(env, PYTHONPATH="tests/pgshim:."),
                       capture_output=True, text=True, timeout=300)
    print("verein-Demo:", (r.stdout or "")[-300:], (r.stderr or "")[-600:], flush=True)
    ids = json.loads(open(os.path.join(st, "handbuch-demo.json")).read())["ids"]
    run.dm.setting("allowed_hosts", "cloud.saga.example, bookorbit.example, wetter.saga.example, verein.example")
    run.dm.setting("fed_enabled", True)
    time.sleep(6)
    for attempt in range(3):
        if fed_connect(run):
            break
        # verein.example sah die Freigabe von saga.example noch nicht: neu setzen (zweiter Server, eigene DB) und warten
        subprocess.run([sys.executable, "-c", "import auth; auth.save_setting('allowed_hosts', 'saga.example')"], cwd=ROOT,
                       env=dict(env, PYTHONPATH="tests/pgshim:."), timeout=60)
        time.sleep(10)
    run.verein = {"ids": ids, "proc": proc}
    return run.verein


def fed_connect(run):
    """Einladung auf verein.example (Admin frieda), verbinden auf saga.example (Admin anna) -> True bei Erfolg."""
    v = Who(run, None, False, DESK, None)
    v.p.goto("http://admin.verein.example/login")
    v.p.fill("input[name=username]", "frieda")
    v.p.fill("input[name=password]", run.dm.pw)
    v.p.click("button[type=submit]")
    v.p.wait_for_load_state("networkidle")
    v.p.goto("http://admin.verein.example/federation")
    v.p.wait_for_load_state("networkidle")
    v.p.locator("button", has_text="Einladungscode erzeugen").first.click()
    v.p.wait_for_load_state("networkidle")
    code = v.p.locator(".fed-code code.fed-fp").first.inner_text().strip()
    v.close()
    a = adm_go(run, user="anna", path="/federation")
    for _ in range(4):                 # der Server sah die neuen "Freigegebenen internen Hosts" manchmal erst spaeter (s. b16-01)
        a.p.fill("input[name=url]", "http://verein.example")
        a.p.fill("input[name=code]", code)
        a.p.locator("button", has_text="Fingerprint prüfen").first.click()
        a.p.wait_for_load_state("networkidle")
        if a.p.locator("button", has_text="Fingerprint stimmt").count():
            break
        print("  Foederation: Vorschau fehlgeschlagen - Freigabe neu setzen und warten", flush=True)
        run.dm.setting("allowed_hosts", "cloud.saga.example, bookorbit.example, wetter.saga.example, verein.example")
        time.sleep(8)
        a.p.goto(run.adm + "/federation")
        a.p.wait_for_load_state("networkidle")
    if a.p.locator("button", has_text="Fingerprint stimmt").count():
        a.p.locator("button", has_text="Fingerprint stimmt").first.click()
        a.p.wait_for_load_state("networkidle")
    a.p.wait_for_timeout(2000)
    fl = a.p.locator("p.adm-flash")
    msg = fl.first.inner_text()[:200] if fl.count() else "-"
    print("Foederation verbinden:", msg, flush=True)
    if "Fehler" in msg:
        a.close()
        return False
    # Gegenseite (verein.example) bestaetigt die Anfrage, dann hier den Status abgleichen
    v = verein_login(run, admin=True)
    v.p.goto("http://admin.verein.example/federation")
    v.p.wait_for_load_state("networkidle")
    b = v.p.locator("button", has_text="Bestätigen")
    if b.count():
        b.first.click()
        v.p.wait_for_load_state("networkidle")
        v.p.wait_for_timeout(1500)
    v.close()
    a.p.goto(run.adm + "/federation")
    a.p.wait_for_load_state("networkidle")
    st = a.p.locator("button", has_text="Status prüfen")
    if st.count():
        st.first.click()
        a.p.wait_for_load_state("networkidle")
        a.p.wait_for_timeout(1500)
    print("Foederation Status:", a.p.locator(".fed-st").first.inner_text() if a.p.locator(".fed-st").count() else "-", flush=True)
    a.close()
    return True


def verein_login(run, user="frieda", admin=False, dark=False):
    w = Who(run, None, dark, DESK, None)
    host = "http://admin.verein.example" if admin else "http://verein.example"
    w.p.goto(host + "/login")
    w.p.fill("input[name=username]", user)
    w.p.fill("input[name=password]", run.dm.pw)
    w.p.click("button[type=submit]")
    w.p.wait_for_load_state("networkidle")
    return w


@scene("b11-01-teilen-foederiert", "familie")
def b11_all(run):
    """Foederation: b11-01 (Frieda teilt mit Anna auf saga.example), b11-02 (Anna bearbeitet die Seite in ihrem Saga),
    a07-01 (Admin -> Foederation auf verein.example), a07-03 (Funktionen je Instanz)."""
    vs = verein_server(run)
    pid = vs["ids"]["parzellen"]
    f = verein_login(run)
    f.p.goto(f"http://verein.example/notes#{pid}")
    f.p.wait_for_load_state("networkidle")
    f.p.wait_for_timeout(2000)
    f.p.click("#pg-share")
    f.p.wait_for_timeout(1200)
    q = f.p.locator("#pg-modal input[type=search], #pg-modal input[type=text]").first
    role = f.p.locator("#pg-modal select").first
    if role.count():
        role.select_option(label="Bearbeiten")       # Rolle vorher waehlen: der Klick auf den Treffer teilt sofort
    q.fill("anna-sommer@saga.example")
    f.p.wait_for_timeout(2500)
    hit = f.p.locator("#pg-modal .share-hit, #pg-modal [role=option], #pg-modal li button").first
    if hit.count():
        hit.click()
        f.p.wait_for_timeout(1500)
    go = f.p.locator("#pg-modal button", has_text="Einladen").first
    if go.is_enabled():
        go.click()
        f.p.wait_for_timeout(2500)
    q.fill("anna-sommer@saga.example")
    f.p.wait_for_timeout(1500)
    f.blur(True)
    dlg = f.p.locator("#pg-modal > *").first
    f.mark([(q, "1", 3), (f.p.locator("#pg-modal li, #pg-modal .share-row", has_text="saga.example").last, "2", 3)])
    run.shot(f.p, "b11-01-teilen-foederiert", el=dlg)
    f.close()
    a = run.who("anna")
    a.go("/notes", 2500)
    sh = a.p.locator("#pg-shared-box")
    sh.locator("text=Parzellenplan").first.click()
    a.p.wait_for_timeout(3000)
    a.blur()
    a.mark([(sh, "1", 3), (a.p.locator("#pg-sharedby"), "2", 3)])
    run.shot(a.p, "b11-02-fremde-seite")
    a.close()
    w = verein_login(run, admin=True)
    w.p.goto("http://admin.verein.example/federation")
    w.p.wait_for_load_state("networkidle")
    w.p.wait_for_timeout(800)
    run.shot(w.p, "a07-01-foederation", full=True)
    card = admin_card(w, "Funktionen je Instanz")
    card.scroll_into_view_if_needed()
    run.shot(w.p, "a07-03-funktionen-je-instanz", el=card, pad=8)
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


# =====================================================================================================
# Chats (Art server) - Familie Sommer: Gruppe "Sommers 🏡" und 1:1 Anna <-> Ben, alles Ende-zu-Ende ueber den Browser
# =====================================================================================================
def send_msg(w, text):
    w.p.fill("#ct-input", text)
    w.p.press("#ct-input", "Enter")
    w.p.wait_for_timeout(900)


def msg_menu(w, text):
    row = w.p.locator(".ct-msg", has_text=text).last
    row.hover()
    row.locator(".ct-act:not(.ct-react-b)").click(force=True)
    w.p.wait_for_timeout(500)
    return row


def chat_world(run):
    """Konten mit Chat-Schluessel, Gruppe (Name verschluesselt, ueber die Oberflaeche angelegt), 1:1 und Nachrichten."""
    import fakes
    ws = {u: run.who(u) for u in ("anna", "ben", "clara", "lena")}
    for w in ws.values():
        keys(run, w)
    a = ws["anna"]
    a.p.click("#ct-new")
    a.p.wait_for_timeout(700)
    a.p.click(".ct-free-toggle")
    for n in ("Ben Sommer", "Clara Sommer", "Oma Lena"):
        a.p.fill(".ct-people-q", n.split()[-1] if n != "Oma Lena" else "Lena")
        until(lambda: a.p.locator(".ct-people-list .ct-person", has_text=n).count(), 8000)
        a.p.locator(".ct-people-list .ct-person", has_text=n).first.click()
        a.p.wait_for_timeout(300)
    a.p.fill(".ct-free input.hk-in", "Sommers 🏡")
    a.p.click(".ct-free-go")
    until(lambda: a.p.locator("#ct-chat:not([hidden])").count(), 10000)
    a.p.wait_for_timeout(1500)
    gid = a.p.evaluate("() => HKChat.state().cur")
    dm = japi(a, "POST", "/api/chats", {"kind": "dm", "user": "ben"})["chat"]["id"]
    run.chats = {"gruppe": gid, "dm": dm}
    G = lambda w: w.go(f"/chats#c{gid}", 1800)  # noqa: E731
    G(a)
    send_msg(a, "Die Einladung fürs Sommerfest steht – Samstag ab 14 Uhr im Garten 🎉")
    b = ws["ben"]
    G(b)
    send_msg(b, "Wer holt Oma am Freitag vom Bahnhof ab?")
    c = ws["clara"]
    G(c)
    msg_menu(c, "Wer holt Oma")
    c.p.locator("button.menu-item", has_text="Antworten").first.click()
    send_msg(c, "Ich! 🙋 Nach der Schule fahre ich mit dem Rad hin.")
    G(b)
    path = "/tmp/hb-grill.png"
    with open(path, "wb") as fh:
        fh.write(fakes.cover_art("blumen", (170, 210, 245), (215, 235, 250), w=1200, h=900))
    b.p.fill("#ct-input", "Der Grill ist sauber – Samstag kann kommen 🔥")
    b.p.set_input_files("#ct-file", path)
    b.p.wait_for_timeout(1500)
    if b.p.locator("#ct-send").is_enabled():
        b.p.click("#ct-send")
    b.p.wait_for_timeout(2500)
    w = ws["lena"]
    G(w)
    send_msg(w, "Ich freue mich auf euch! Der Apfelkuchen ist schon geplant 🍎")
    G(a)
    msg_menu(a, "Ich! 🙋")
    a.p.locator(".ct-rq-b", has_text="❤️").first.click()
    a.p.wait_for_timeout(800)
    msg_menu(a, "Die Einladung fürs Sommerfest")
    a.p.locator("button.menu-item", has_text="Anheften").first.click()
    a.p.wait_for_timeout(1000)
    a.go(f"/chats#c{dm}", 1500)
    send_msg(a, "Denkst du an die Fähre? Die Frühbucherpreise enden am Sonntag.")
    b.go(f"/chats#c{dm}", 1500)
    send_msg(b, "Ist gebucht ✔ Hin am 17., zurück am 31.")
    return ws


@scene("b12-01-chatliste", "familie")
def b12_all(run):
    """Alle Chat-Bilder (b12-01 ... b12-10) und a08-02 (Meldung) aus einer Chat-Welt."""
    ws = chat_world(run)
    a, gid = ws["anna"], run.chats["gruppe"]
    a.go("/chats", 2500)
    no_toasts(a)
    run.shot(a.p, "b12-01-chatliste")
    # b12-02 Familienchat
    a.go(f"/chats#c{gid}", 3000)
    no_toasts(a)
    a.mark([(a.p.locator(".ct-msg", has_text="Ich! 🙋").last.locator(".ct-quote, .ct-reply-q, blockquote").first, "1", 3),
            (a.p.locator(".ct-msg", has_text="Ich! 🙋").last.locator(".ct-rx, .ct-rxs").first, "2", 3),
            (a.p.locator(".ct-msg img").last, "3", 3)])
    run.shot(a.p, "b12-02-familienchat")
    a.unmark()
    # b12-07 Angeheftet (oberer Teil)
    a.mark([(a.p.locator("#ct-pins"), "1", 2)])
    run.shot(a.p, "b12-07-angeheftet", el={"x": 0, "y": 0, "width": 1440, "height": 260})
    a.unmark()
    # b12-03 Gruppeninfo
    a.p.click("#ct-title")
    a.p.wait_for_timeout(1500)
    a.mark([(a.p.locator(".ct-gi-head").first, "1", 3), (a.p.locator(".ct-gi-perms").first, "2", 3),
            (a.p.locator(".ct-gi-foot button", has_text="Medien").first, "3", 3)])
    run.shot(a.p, "b12-03-gruppeninfo")
    a.unmark()
    a.p.keyboard.press("Escape")
    a.p.wait_for_timeout(500)
    # b12-05 Neue Unterhaltung (Kontakte)
    a.p.click("#ct-new")
    a.p.wait_for_timeout(1500)
    a.blur(True)
    a.mark([(a.p.locator(".ct-people-h").first, "1", 3)])
    run.shot(a.p, "b12-05-kontakte")
    a.unmark()
    a.p.keyboard.press("Escape")
    a.p.wait_for_timeout(500)
    # b12-10 Rechtsklick in der Chatliste
    a.go("/chats", 2000)
    it = a.p.locator(".ct-item-b", has_text="Ben Sommer").first
    it.click(button="right")
    a.p.wait_for_timeout(700)
    a.mark([(menu_item(a, "Archivieren"), "1", 2)])
    run.shot(a.p, "b12-10-archiv")
    a.close()
    # b12-04 Gruppeninfo auf Bens Handy (dunkel)
    for u in ("ben", "clara", "lena"):
        ws[u].close()
    b = run.who("ben", dark=True, form=PHONE)
    keys(run, b)
    b.go(f"/chats#c{gid}", 2500)
    b.p.click("#ct-title")
    b.p.wait_for_timeout(1500)
    run.shot(b.p, "b12-04-gruppeninfo-handy")
    b.close()
    # b12-08/09 Claras neuer Browser ohne Chat-Schluessel
    c = run.who("clara")
    c.go("/chats", 2500)
    c.mark([(c.p.locator(".ct-pair-go, button", has_text="Mit anderem Gerät").first, "1", 3),
            (c.p.locator("button", has_text="Wiederherstellungsschlüssel eingeben").first, "2", 3)])
    run.shot(c.p, "b12-08-geraet-bestaetigen")
    c.unmark()
    c.p.locator(".ct-rk-go").first.click()
    c.p.fill(".ct-rk-in", "ABCD EFGH JKLM NPQR STUV WXYZ 2345 6723")       # erfundener Schluessel
    c.blur(True)
    c.mark([(c.p.locator(".ct-rk-in"), "1", 3)])
    run.shot(c.p, "b12-09-wiederherstellungsschluessel")
    c.close()
    # a08-02 Meldung: Oma Lena meldet eine Nachricht von Ben (Text der meldenden Person, keine Inhalte)
    with run.dm.db.connect() as cc:
        mid = cc.execute("SELECT id FROM chat_msg WHERE chat_id=? AND kind='msg' AND sender_uid='ben' ORDER BY ts LIMIT 1", (gid,)).fetchone()
    if mid:
        run.dm.api("lena", "POST", "/api/chat/report", {"chat": gid, "msgs": [mid[0]], "reason": "spam", "text": "Ausprobiert für das Handbuch"})
        w = adm_go(run, user="anna", path="/reports")
        run.shot(w.p, "a08-02-meldungen")
        w.close()


def ctl(w, label):
    return w.p.locator(f".cl-ctl button[aria-label='{label}']").first


@scene("b13-01-anruf-video", "familie", "knoten")
def b13_01(run):
    """b13-05 (Ben wird angerufen, Handy dunkel), b13-01 (Videoanruf, Annas Sicht dunkel), b13-02 (Ben teilt den Bildschirm)."""
    a = caller(run, "anna")
    b = caller(run, "ben", form=dict(PHONE, user_agent=ANDROID_UA))
    cid = japi(a, "POST", "/api/chats", {"kind": "dm", "user": "ben"})["chat"]["id"]
    b.go(f"/chats#c{cid}", 1500)
    call_from_chat(a, cid)
    b.p.wait_for_selector(".cl-ring", timeout=20000)
    b.p.wait_for_timeout(1500)
    no_toasts(b)
    b.mark([(b.p.locator(".cl-ring .cl-red").first, "1", 3), (b.p.locator(".cl-ring .cl-big").first, "2", 3)])
    run.shot(b.p, "b13-05-klingeln-handy")
    b.unmark()
    b.p.click(".cl-ring .cl-big")
    until(lambda: (room(a) or {}).get("inRoom") and (room(b) or {}).get("inRoom"), 25000)
    a.p.wait_for_timeout(5000)
    no_toasts(a)
    a.mark([(ctl(a, "Stummschalten"), "1", 3), (ctl(a, "Kamera aus"), "2", 3), (ctl(a, "Bildschirm teilen"), "3", 3),
            (a.p.locator(".cl-ctl .cl-red").first, "4", 3)])
    run.shot(a.p, "b13-01-anruf-video")
    a.unmark()
    hangup(b, a)
    a.p.wait_for_timeout(2000)
    b.close()
    # b13-02: Ben am Laptop teilt seinen Bildschirm, Anna (hell) sieht ihn gross
    a.close()
    a = caller(run, "anna", dark=False)
    b = caller(run, "ben")
    b.go(f"/chats#c{cid}", 1500)
    call_from_chat(a, cid)
    accept(b)
    until(lambda: (room(a) or {}).get("inRoom"), 15000)
    b.p.wait_for_timeout(2500)
    ctl(b, "Bildschirm teilen").click()
    if until(lambda: b.p.locator(".cl-hint .cl-hint-go").count(), 3000):
        b.p.click(".cl-hint .cl-hint-go")
    a.p.wait_for_timeout(6000)
    no_toasts(a)
    run.shot(a.p, "b13-02-bildschirm-teilen")
    hangup(b, a)
    a.p.wait_for_timeout(1500)
    a.close()
    b.close()


@scene("b13-03-gruppenanruf", "familie", "knoten")
def b13_03(run):
    ws = [caller(run, u) for u in ("anna", "ben", "clara", "lena")]
    a = ws[0]
    cid = japi(a, "POST", "/api/chats", {"kind": "free", "members": ["ben", "clara", "lena"], "device": device(a)})["chat"]["id"]
    for w in ws[1:]:
        w.go("/chats", 1500)
    call_from_chat(a, cid)
    for w in ws[1:]:
        accept(w)
    until(lambda: len(set((room(a) or {}).get("legs") or [])) >= 4, 30000)
    a.p.wait_for_timeout(6000)
    no_toasts(a)
    run.shot(a.p, "b13-03-gruppenanruf")
    hangup(*reversed(ws))
    a.p.wait_for_timeout(2000)
    for w in ws:
        w.close()


@scene("b13-04-hierher-uebernehmen", "familie", "knoten")
def b13_04(run):
    a = caller(run, "anna")
    ph = run.who("ben", form=dict(PHONE, user_agent=ANDROID_UA), media=MEDIA["ben"])
    keys(run, ph)
    lap = run.who("ben", media=MEDIA["ben"])
    keys(run, lap)
    cid = japi(a, "POST", "/api/chats", {"kind": "dm", "user": "ben"})["chat"]["id"]
    ph.go(f"/chats#c{cid}", 1500)
    call_from_chat(a, cid)
    accept(ph)
    lap.page(run.ids["radtour"], 3000)
    lap.p.wait_for_selector(".cl-run", timeout=20000)
    lap.p.wait_for_timeout(1500)
    no_toasts(lap)
    lap.mark([(lap.p.locator(".cl-run .cl-run-go").first, "1", 3)])
    run.shot(lap.p, "b13-04-hierher-uebernehmen")
    hangup(ph, a)
    a.p.wait_for_timeout(1500)
    for w in (a, ph, lap):
        w.close()


@scene("b15-03-ki-chat", "familie")
def b15_03(run):
    """KI im Familienchat auf Zuruf: Anna erlaubt die KI im Chat und fragt "@Saga KI …" - die Antwort sehen alle."""
    ws = chat_world(run)
    a, gid = ws["anna"], run.chats["gruppe"]
    for u in ("ben", "clara", "lena"):
        ws[u].close()
    r = japi(a, "PUT", f"/api/chats/{gid}/ai", {"allowed": True})
    print("  KI im Chat:", str(r)[:120], flush=True)
    a.go(f"/chats#c{gid}", 2500)
    a.p.fill("#ct-input", "@Saga KI was fehlt noch fürs Sommerfest?")
    a.p.press("#ct-input", "Enter")
    until(lambda: a.p.locator(".ct-msg.ct-ai").count(), 30000)
    a.p.wait_for_timeout(3000)
    no_toasts(a)
    run.shot(a.p, "b15-03-ki-chat")
    a.close()


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
    for pr in getattr(run, "procs", []):              # zweiter Server (Foederation) und seine DB
        pr.terminate()
        try:
            pr.wait(20)
        except Exception:  # noqa: BLE001
            pr.kill()
    if getattr(run, "verein_db", None):
        import psycopg
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
            c.execute(f"DROP DATABASE IF EXISTS {run.verein_db} WITH (FORCE)")
    optimize(run.out, [m for m in run.made if not m.startswith("x-")])
    print("Bilder:", len(run.made), "Fehler:", ",".join(fail) or "-", "Ordner:", run.out, flush=True)
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
