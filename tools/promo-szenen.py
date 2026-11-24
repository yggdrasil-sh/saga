#!/usr/bin/env python3
"""Rohmaterial fuer das Saga-Promovideo (2.5): kurze, echte Ablaeufe in der Demo-Welt, im Browser gefilmt.

Aufruf auf der Testmaschine (root, wie tools/handbuch-stack.sh):
  tools/promo-szenen.py [--nur 3,6]        Szenen 1-9 (ohne --nur alle); startet die Testumgebung (Art knoten) und
                                           dieses Skript darin (Netz-Namensraum, eigene DB, Anzeige-Hosts *.example)
  tools/promo-szenen.py --liste            Szenen zeigen
Variablen wie handbuch-stack.sh: PROMO_SLOT (Slot der Testumgebung, Standard 7), UMG_NS (Standard pv), UMG_DB (Standard
saga_promo), UMG_BIN, HB_DIR (Arbeitsordner, Standard /srv/saga-test/tmp/promo), HB_FONTS; PROMO_OUT (Zielordner, Standard
$HB_DIR/roh), PROMO_TZ (Zeitzone der Browser; Standard: die Zone, in der es gerade Nachmittag ist - die Uhrzeiten im Bild
wirken dann wie am Tag), PROMO_FPS (30).

Ergebnis je Szene: <PROMO_OUT>/szene-<nr>-<name>/00000.jpg ... (konstant PROMO_FPS, 1920x1080; Handy 1170x2532) mit
zeiten.txt (Bild ; Sekunden seit Szenenstart ; Zeit des Quellbilds) und 2-3 Standbildern standbild-<n>.png in voller Groesse.
Gefilmt wird per CDP Page.startScreencast (JPEG, Qualitaet 92, jedes Bild mit Zeitstempel) - nicht per recordVideo; Maus und
Tippen steuert das Skript gleichmaessig (Mauszeiger als eingeblendeter Pfeil, headless gibt es keinen).

Demo-Welt (englisch, Benutzersprache en): Sommer family auf saga.example (anna = Admin, ben, clara, lena), Lindenhof garden
club auf verein.example (frieda, paul; nur Szene 8). Nur erfundene Inhalte, gezeichnete Bilder, Kamera = gezeichnete
Initialen. Szenen:
  1 heute         Today mit Seitenleiste, Terminen (CalDAV-Attrappe Radicale) und Aufgaben, Aufgabe abhaken
  2 seite         Family Wiki (Titelbild aus der Galerie, Siegel): Text tippen, Checkliste einfuegen, Unterseiten-Galerie
  3 live          Anna und Ben bearbeiten gleichzeitig dieselbe Seite (zwei Fenster 960x1080 nebeneinander)
  4 datenbank     Chores-Board: Karte verschieben, Eigenschaft aendern
  5 chat          Familien-Chat: Nachricht kommt an, Reaktion, Antwort, Sprachnachricht
  6 anruf         Gruppenanruf zu dritt ueber den eingebauten Knoten, Sprecher-Ansicht, Bildschirm teilen
  7 dunkel/handy  dunkler Modus (Desktop) und Handy 390x844 Faktor 3
  8 foederation   Ben chattet mit Frieda vom Gartenverein (zweiter Server verein.example)
  9 admin         Admin -> Calls: eingebauter Knoten"""
import base64
import importlib.util
import json
import os
import random
import secrets
import shutil
import subprocess
import sys
import time
import traceback
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "handbuch-fakes"))

NAMES = {1: "heute", 2: "seite", 3: "live", 4: "datenbank", 5: "chat", 6: "anruf", 7: "dunkel", 8: "foederation", 9: "admin"}
FPS = int(os.environ.get("PROMO_FPS") or 30)
# Bildgroesse = CSS-Viewport x Faktor. Der Screencast liefert Bilder in CSS-Pixeln mal --force-device-scale-factor des
# Browsers (nicht mal device_scale_factor des Kontexts) - deshalb startet je Faktor ein eigener Browser (Run.use).
FULL = {"viewport": {"width": 1280, "height": 720}, "device_scale_factor": 1.5}          # 1920x1080, gut lesbar
CALL = {"viewport": {"width": 1920, "height": 1080}, "device_scale_factor": 1}           # Anruf (Szene 6): Kacheln gross genug
HALF = {"viewport": {"width": 768, "height": 864}, "device_scale_factor": 1.25}          # je Fenster 960x1080 (Szene 3)
PHONE = {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 3, "is_mobile": True, "has_touch": True}
HOSTS = ("127.0.1.1 saga.example\n127.0.1.2 admin.saga.example\n127.0.1.7 verein.example\n127.0.1.8 admin.verein.example\n"
         "127.0.1.9 cal.saga.example\n127.0.0.1 node.saga.example")
NODE = "node.saga.example"
random.seed(25)


def nur_arg(argv):
    if "--nur" in argv:
        return sorted({int(x) for x in argv[argv.index("--nur") + 1].replace(" ", ",").split(",") if x.strip()})
    v = os.environ.get("PROMO_NUR") or ""
    return sorted({int(x) for x in v.split(",") if x.strip()}) or sorted(NAMES)


# =====================================================================================================
# Aussen: Testumgebung starten (wie tools/handbuch-stack.sh)
# =====================================================================================================
def aussen(nur):
    slot = os.environ.get("PROMO_SLOT", "7")
    hb = os.environ.get("HB_DIR") or "/srv/saga-test/tmp/promo"
    env = dict(os.environ, UMG_NS=os.environ.get("UMG_NS", "pv"), UMG_DB=os.environ.get("UMG_DB", "saga_promo"), HB_DIR=hb)
    env["HB_FONTS"] = fonts = os.environ.get("HB_FONTS") or os.path.join(hb, "fonts")
    env["PROMO_OUT"] = out = os.environ.get("PROMO_OUT") or os.path.join(hb, "roh")
    os.makedirs(fonts, exist_ok=True)
    os.makedirs(out, exist_ok=True)
    runic = os.path.join(fonts, "NotoSansRunic-Regular.ttf")
    if not (os.path.exists(runic) and os.path.getsize(runic)):               # Runensiegel im Anruf (sonst Tofu)
        subprocess.run(["curl", "-sfL", "-o", runic, "https://github.com/notofonts/notofonts.github.io/raw/main/fonts/NotoSansRunic/"
                        "hinted/ttf/NotoSansRunic-Regular.ttf"], check=False)
    env["UMG_PYX"] = os.environ.get("UMG_PYX") or os.environ.get("UMG_PWLIB") or "/srv/saga-test/tmp/suite/pylib"
    env["UMG_HOSTS"] = HOSTS
    umg = os.environ.get("UMG") or "/srv/saga-test/tmp/umgebung"
    if not env.get("UMG_BIN"):                       # Knoten-Binaries wie im Image (Version aus VERSION)
        rev = subprocess.run(["git", "-C", ROOT, "log", "-1", "--format=%h", "--", "gjallarhorn", "media", "VERSION"],
                             capture_output=True, text=True).stdout.strip()
        b = env["UMG_BIN"] = os.path.join(hb, "bin-" + rev)
        if not (os.access(os.path.join(b, "gjallarhorn"), os.X_OK) and os.access(os.path.join(b, "saga-media"), os.X_OK)):
            os.makedirs(b, exist_ok=True)
            v = open(os.path.join(ROOT, "VERSION")).read().strip()
            for d, n in (("gjallarhorn", "gjallarhorn"), ("media", "saga-media")):
                subprocess.run(["go", "build", "-trimpath", "-ldflags", f"-s -w -X main.version={v}", "-o", os.path.join(b, n), "."],
                               cwd=os.path.join(ROOT, d), env=dict(env, CGO_ENABLED="0"), check=True)
    # Zertifikat des eingebauten Knotens fuer node.saga.example (Test-CA der Umgebung, 30 Tage, nur im Lauf)
    hc = os.path.join(hb, "node-saga")
    if not os.path.exists(hc + ".pem") and os.path.exists(os.path.join(umg, "ca", "ca.key")):
        sh = (f'openssl req -newkey rsa:2048 -nodes -subj "/CN={NODE}" -keyout {hc}.key -out {hc}.csr 2>/dev/null && '
              f"printf 'subjectAltName=DNS:{NODE},DNS:localhost\\nextendedKeyUsage=serverAuth\\nbasicConstraints=CA:FALSE\\n' > {hc}.ext && "
              f"openssl x509 -req -in {hc}.csr -CA {umg}/ca/ca.pem -CAkey {umg}/ca/ca.key -CAserial {hc}.srl -CAcreateserial -days 30 "
              f"-extfile {hc}.ext -out {hc}.pem 2>/dev/null && chmod 600 {hc}.key")
        subprocess.run(["bash", "-c", sh], check=False)
    if os.path.exists(hc + ".pem"):
        env.update(UMG_HORN_NAME=NODE, UMG_HORN_CERT=hc + ".pem", UMG_HORN_KEY=hc + ".key")
    env["UMG_SENV"] = ("BASE_URL=http://saga.example ADMIN_URL=http://admin.saga.example CACHE_TTL=3 FED_ALLOW_HTTP=1 "
                       + os.environ.get("HB_SENV", ""))
    env.update(PROMO_INNEN="1", PROMO_NUR=",".join(map(str, nur)), UMG_LIMIT=os.environ.get("UMG_LIMIT", "3000"))
    tag = env["UMG_NS"] + slot + "-promo"
    print("== Szenen", nur, "Slot", slot, "Ausgabe", out, flush=True)
    rc = subprocess.run(["bash", os.path.join(HERE, "testumgebung", "umgebung.sh"), "lauf", "knoten", slot, os.path.abspath(__file__), tag],
                        env=env).returncode
    log = os.path.join(umg, "log", f"promo-szenen.py.{tag}.log")
    if os.path.exists(log):
        for ln in open(log, errors="replace"):
            if ln.startswith(("OK", "FAIL", "SZENE", "Szenen:", "FEHLER")) or "bereit=" in ln:
                print("  " + ln.rstrip())
    return rc


# =====================================================================================================
# Innen: Handbuch-Werkzeuge wiederverwenden (Run, Who, Chat- und Anruf-Helfer)
# =====================================================================================================
def _load(name, fn):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, fn))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


SH = _load("screens_handbuch", "screens-handbuch.py")
HD = _load("handbuch_demo", "handbuch-demo.py")


def pick_tz():
    """Zeitzone der Browser: wo es gerade Nachmittag ist (Uhrzeiten im Bild wirken wie am Tag) - oder PROMO_TZ."""
    from zoneinfo import ZoneInfo
    if os.environ.get("PROMO_TZ"):
        return os.environ["PROMO_TZ"]
    best = None
    now = datetime.now(timezone.utc)
    for z in ("Europe/London", "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles", "Pacific/Honolulu",
              "Pacific/Auckland", "Australia/Sydney", "Asia/Tokyo", "Asia/Singapore", "Asia/Kolkata", "Europe/Berlin"):
        lt = now.astimezone(ZoneInfo(z))
        d = abs(lt.hour + lt.minute / 60 - 14.5)
        if best is None or d < best[0]:
            best = (d, z)
    return best[1]


TZ = pick_tz()


def local_now():
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo(TZ))


# Mauszeiger (headless gibt es keinen): Pfeil folgt mousemove/dragover, Klick-Kreis; beim Ziehen eine halbdurchsichtige
# Kopie der Karte wie das Ziehbild des Browsers. Handy (window.__promoTouch): nur der Tipp-Kreis.
CURSOR_JS = r"""
(() => {
  if (window.__promoCursor) return; window.__promoCursor = 1;
  const SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="26" height="34" viewBox="0 0 26 34"><path d="M3 2 L3 26 L9 20.5 L13.2 30 L17.2 28.2 L13 18.8 L21.5 18.8 Z" fill="#161616" stroke="#fff" stroke-width="1.8" stroke-linejoin="round"/></svg>';
  let c = null, ghost = null, x = -100, y = -100;
  const touch = () => !!(window.__promoTouch || (window.top !== window && (() => { try { return window.top.__promoTouch; } catch (e) { return false; } })()));
  // In eingebetteten Rahmen (z. B. Seitenansicht einer Datenbank-Zeile) zeichnet nur das oberste Dokument den Zeiger
  const up = (fn, px, py) => { try { const r = window.frameElement.getBoundingClientRect(); window.parent[fn](px + r.left, py + r.top); return true; } catch (e) { return false; } };
  function ensure() {
    if (c && c.isConnected) return c;
    c = document.createElement('div'); c.id = 'promo-cursor'; c.innerHTML = SVG;
    Object.assign(c.style, { position: 'fixed', left: '0', top: '0', width: '26px', height: '34px', zIndex: 2147483647, pointerEvents: 'none',
      filter: 'drop-shadow(0 1px 2px rgba(0,0,0,.35))', display: 'none', willChange: 'transform' });
    (document.body || document.documentElement).appendChild(c); return c;
  }
  window.__promoMove = (px, py) => {
    if (window.top !== window && up('__promoMove', px, py)) return;
    x = px; y = py; if (touch()) return;
    const el = ensure(); el.style.display = 'block'; el.style.transform = 'translate(' + (x - 3) + 'px,' + (y - 2) + 'px)';
    if (ghost) ghost.style.transform = 'translate(' + (x - ghost._ox) + 'px,' + (y - ghost._oy) + 'px) rotate(2deg)';
  };
  window.__promoRipple = (px, py) => {
    if (window.top !== window && up('__promoRipple', px, py)) return;
    const r = document.createElement('div'), s = touch() ? 44 : 30;
    Object.assign(r.style, { position: 'fixed', left: (px - s / 2) + 'px', top: (py - s / 2) + 'px', width: s + 'px', height: s + 'px', borderRadius: '50%',
      background: touch() ? 'rgba(120,120,120,.35)' : 'rgba(80,80,80,.22)', border: '2px solid rgba(80,80,80,.45)', zIndex: 2147483646,
      pointerEvents: 'none', transform: 'scale(.4)', opacity: '1', transition: 'transform .38s ease-out, opacity .45s ease-out' });
    (document.body || document.documentElement).appendChild(r);
    requestAnimationFrame(() => requestAnimationFrame(() => { r.style.transform = 'scale(1.25)'; r.style.opacity = '0'; }));
    setTimeout(() => r.remove(), 600);
  };
  function mv(e) { if (e.clientX === 0 && e.clientY === 0) return; window.__promoMove(e.clientX, e.clientY); }
  ['mousemove', 'pointermove', 'dragover', 'drag', 'dragenter'].forEach(t => addEventListener(t, mv, true));
  addEventListener('pointerdown', e => window.__promoRipple(e.clientX, e.clientY), true);
  addEventListener('dragstart', e => {
    if (window.top !== window) return;
    const src = e.target && e.target.closest && e.target.closest('.board-card, [draggable="true"]'); if (!src) return;
    const b = src.getBoundingClientRect(); ghost = src.cloneNode(true);
    ghost._ox = e.clientX - b.left; ghost._oy = e.clientY - b.top;
    Object.assign(ghost.style, { position: 'fixed', left: '0', top: '0', width: b.width + 'px', margin: '0', zIndex: 2147483645, pointerEvents: 'none',
      opacity: '.85', boxShadow: '0 12px 28px rgba(0,0,0,.22)' });
    document.body.appendChild(ghost); window.__promoMove(e.clientX, e.clientY);
  }, true);
  const drop = () => { if (ghost) { ghost.remove(); ghost = null; } };
  addEventListener('drop', drop, true); addEventListener('dragend', drop, true);
})();
"""

# Kamera/Mikrofon/Bildschirm wie im Handbuch (gezeichnete Initialen, gezeichnete Fotos), Texte englisch; who.voice: Mikrofon
# mit sprachartig schwankendem Pegel (Sprachnachricht mit Wellenform, Sprecher-Ansicht im Anruf) statt Stille
MEDIA_JS = (SH.MEDIA_JS.replace("Fotos Ostsee 2026", "Holiday photos 2026")
            .replace("Mikrofon (Standard)", "Microphone (default)").replace("Kamera (Standard)", "Camera (default)")
            .replace("Lautsprecher (Standard)", "Speakers (default)")
            .replace("gn.gain.value = 0.0001;", "gn.gain.value = 0.0001; if (who.voice) { o.type = 'sawtooth'; o.frequency.value = 150; "
                     "gn.gain.value = 0.22; [[3.1, 0.2], [0.55, 0.12], [7.3, 0.06]].forEach(([f, a]) => { const l = ac.createOscillator(), lg = ac.createGain(); "
                     "l.frequency.value = f; lg.gain.value = a; l.connect(lg); lg.connect(gn.gain); l.start(); }); }"))
assert "who.voice" in MEDIA_JS and "Holiday photos" in MEDIA_JS
MEDIA = {"anna": {"ini": "AS", "c1": "#f08050", "c2": "#e85c70"}, "ben": {"ini": "BS", "c1": "#4682d2", "c2": "#3c5aaa"},
         "clara": {"ini": "CS", "c1": "#46aa6e", "c2": "#287a5a"}, "lena": {"ini": "GL", "c1": "#be6ec8", "c2": "#8c50aa"},
         "frieda": {"ini": "FL", "c1": "#5a9650", "c2": "#326e3c"}}


class P(SH.Who):
    """Person in eigenem Browser-Kontext, englisch (en-US), Zeitzone TZ, Mauszeiger eingeblendet."""

    def __init__(self, run, user, dark=False, form=FULL, media=None, base=None, voice=False):
        self.run, self.user, self.dark = run, user, dark
        self.base = base or run.base
        run.use(form.get("device_scale_factor", 1))
        self.ctx = run.b.new_context(color_scheme="dark" if dark else "light", locale="en-US", timezone_id=TZ,
                                     ignore_https_errors=True, **form)
        self.ctx.grant_permissions(["camera", "microphone", "notifications"], origin=self.base)
        self.ctx.add_init_script(CURSOR_JS + ("window.__promoTouch = 1;" if form.get("has_touch") else ""))
        if media:
            self.ctx.add_init_script("window.__hbWho = " + json.dumps(dict(media, voice=voice)) + ";\n" + MEDIA_JS)
        self.ctx.add_init_script("if (window.PublicKeyCredential) PublicKeyCredential.isConditionalMediationAvailable = () => Promise.resolve(false);")
        if user and self.base == run.base:
            self.ctx.add_cookies([{"name": "saga", "value": run.dm.ck(user), "domain": run.host, "path": "/"}])
        self.p = self.ctx.new_page()
        self.p.on("pageerror", lambda e: print(" ", user, "pageerror", str(e)[:200], flush=True))
        self.form = form
        self.mx, self.my = form["viewport"]["width"] * 0.55, form["viewport"]["height"] * 0.6
        run.open.append(self)

    def go(self, path, wait=1200):
        self.p.goto(self.base + path)
        self.p.wait_for_load_state("networkidle")
        self.p.wait_for_timeout(wait)

    def quiet(self):
        """Glocke leeren (Mitteilungen aus dem Aufbau der Demo-Welt als gelesen) und Hinweise ausblenden."""
        if self.user and self.base in (self.run.base, self.run.adm):
            self.run.dm.api(self.user, "POST", "/api/notifications/read", {"all": True})
        self.hide_toasts()

    def hide_toasts(self):
        """Hinweise (Toasts) ausblenden - als eigenes Stylesheet per CSSOM (die CSP erlaubt keine Inline-Styles)."""
        self.p.evaluate("""() => { const s = new CSSStyleSheet(); s.replaceSync('.toast, .hk-toast, #toasts { display: none !important; }');
          document.adoptedStyleSheets = [...document.adoptedStyleSheets, s]; }""")

    # --- Bewegung ---------------------------------------------------------------------------------------------------------
    def wait(self, ms):
        self.p.wait_for_timeout(ms)

    def glide(self, x, y, ms=None):
        x0, y0 = self.mx, self.my
        dist = ((x - x0) ** 2 + (y - y0) ** 2) ** 0.5
        ms = ms or max(380, min(1100, 320 + dist * 0.55))
        n = max(10, int(ms / 16))
        t0 = time.time()
        for i in range(1, n + 1):
            t = i / n
            e = 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2
            self.p.mouse.move(x0 + (x - x0) * e, y0 + (y - y0) * e)
            rest = t0 + ms / 1000 * t - time.time()
            if rest > 0.004:
                self.p.wait_for_timeout(int(rest * 1000))
        self.mx, self.my = x, y

    def point(self, loc, fx=0.5, fy=0.5, dx=0, dy=0):
        bb = loc.bounding_box(timeout=8000)
        return bb["x"] + bb["width"] * fx + dx, bb["y"] + bb["height"] * fy + dy

    def to(self, loc, fx=0.5, fy=0.5, dx=0, dy=0, ms=None):
        x, y = self.point(loc, fx, fy, dx, dy)
        self.glide(x, y, ms)
        return x, y

    def click(self, loc, fx=0.5, fy=0.5, dx=0, dy=0, ms=None, pause=140):
        self.to(loc, fx, fy, dx, dy, ms)
        self.p.wait_for_timeout(pause)
        self.p.mouse.down()
        self.p.wait_for_timeout(80)
        self.p.mouse.up()

    def tap(self, loc, fx=0.5, fy=0.5):
        x, y = self.point(loc, fx, fy)
        self.p.touchscreen.tap(x, y)

    def typ(self, text, cps=12):
        for ch in text:
            self.p.keyboard.type(ch)
            d = 1000 / cps * random.uniform(0.65, 1.35) * (1.6 if ch in " .,!?" else 1)
            self.p.wait_for_timeout(int(d))

    def scroll(self, dy, ms=900, el=None):
        """Weich scrollen (Mausrad in kleinen Schritten)."""
        n = max(8, int(ms / 30))
        for i in range(n):
            self.p.mouse.wheel(0, dy / n)
            self.p.wait_for_timeout(int(ms / n))


# =====================================================================================================
# Aufnahme (CDP-Screencast) und Ausgabe mit konstanter Bildrate
# =====================================================================================================
class Rec:
    def __init__(self, w, out, label=""):
        self.w, self.out, self.label = w, out, label
        vp, dsf = w.form["viewport"], w.form.get("device_scale_factor", 1)
        self.size = (int(vp["width"] * dsf), int(vp["height"] * dsf))
        self.frames, self.stills = [], []
        self.cdp = w.ctx.new_cdp_session(w.p)
        self.cdp.on("Page.screencastFrame", self._frame)
        self.on = False

    def _frame(self, ev):
        ts = (ev.get("metadata") or {}).get("timestamp") or time.time()
        if self.on:
            self.frames.append((ts, ev["data"]))
        try:
            self.cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})
        except Exception:  # noqa: BLE001
            pass

    def start(self):
        self.cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 92, "maxWidth": self.size[0], "maxHeight": self.size[1],
                                               "everyNthFrame": 1})
        self.on = True
        self.t0 = time.time()
        # ein Bild sofort (der Screencast schickt nur bei Aenderungen): winzige Aenderung im Dokument
        self.w.p.evaluate("() => { const d = document.createElement('i'); d.style.cssText = 'position:fixed;left:0;top:0;width:1px;height:1px;opacity:.01;pointer-events:none'; document.documentElement.appendChild(d); setTimeout(() => d.remove(), 120); }")
        self.w.p.wait_for_timeout(150)

    def still(self):
        """Standbild in voller Groesse (PNG) - an den vollsten Stellen."""
        b = self.w.p.screenshot(type="png")
        self.stills.append(b)
        return b

    def stop(self):
        self.w.p.wait_for_timeout(120)
        self.t1 = time.time()
        self.on = False
        try:
            self.cdp.send("Page.stopScreencast")
        except Exception:  # noqa: BLE001
            pass

    def pick(self, t):
        """Index des letzten Bilds bis Zeitpunkt t (Sekunden seit Start)."""
        fr, j = self.frames, getattr(self, "_j", 0)
        ab = self.t0 + t
        if j >= len(fr) or (j and fr[j - 1][0] > ab):
            j = 0
        while j + 1 < len(fr) and fr[j + 1][0] <= ab:
            j += 1
        self._j = j
        return j


def write_scene(name, recs, out_root, layout=None, trim=(0.0, 0.0)):
    """recs: eine Aufnahme (ganze Flaeche) oder zwei (nebeneinander, je 960x1080 -> 1920x1080). Konstante Bildrate."""
    from PIL import Image, ImageDraw
    d = os.path.join(out_root, name)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    t0 = max(r.t0 for r in recs) + trim[0]
    t1 = min(r.t1 for r in recs) - trim[1]
    n = max(1, int((t1 - t0) * FPS))
    if not all(r.frames for r in recs):
        raise RuntimeError(f"{name}: keine Bilder aus dem Screencast ({[len(r.frames) for r in recs]})")
    lines, prev, prev_key = [], None, None
    cache = {}
    for i in range(n):
        t = t0 + i / FPS
        idx = [r.pick(t - r.t0) for r in recs]
        key = tuple(idx)
        fn = os.path.join(d, f"{i:05d}.jpg")
        if key == prev_key and prev:
            os.link(prev, fn)                               # gleiches Bild: harter Link (tar speichert es einmal)
        elif len(recs) == 1:
            with open(fn, "wb") as fh:
                fh.write(base64.b64decode(recs[0].frames[idx[0]][1]))
        else:
            im = Image.new("RGB", (1920, 1080), (40, 40, 40))
            for k, (r, j) in enumerate(zip(recs, idx)):
                ck = (k, j)
                if ck not in cache:
                    import io
                    cache = {kk: vv for kk, vv in cache.items() if kk[0] != k}
                    cache[ck] = Image.open(io.BytesIO(base64.b64decode(r.frames[j][1]))).convert("RGB")
                src = cache[ck]
                if src.size != (960, 1080):
                    src = src.resize((960, 1080))
                im.paste(src, (k * 960, 0))
            ImageDraw.Draw(im).rectangle([958, 0, 961, 1080], fill=(60, 60, 60))
            im.save(fn, "JPEG", quality=92)
        lines.append(f"{i:05d}.jpg ; {i / FPS:.3f} ; " + " ".join(f"{recs[k].frames[j][0] - recs[k].t0:.3f}" for k, j in enumerate(idx)))
        prev, prev_key = fn, key
    with open(os.path.join(d, "zeiten.txt"), "w") as fh:
        fh.write(f"# {name}: {n} Bilder, {FPS} fps konstant, {n / FPS:.2f} s; Spalten: Bild ; Sekunden seit Szenenstart ; "
                 f"Zeit des Quellbilds (Screencast, je Fenster)\n" + "\n".join(lines) + "\n")
    # Standbilder: je Aufnahme; bei zwei Fenstern nebeneinander zusammengesetzt
    import io
    k = 0
    for parts in zip(*[r.stills for r in recs]):
        k += 1
        if len(parts) == 1:
            open(os.path.join(d, f"standbild-{k}.png"), "wb").write(parts[0])
        else:
            im = Image.new("RGB", (1920, 1080), (40, 40, 40))
            for j, b in enumerate(parts):
                im.paste(Image.open(io.BytesIO(b)).convert("RGB"), (j * 960, 0))
            ImageDraw.Draw(im).rectangle([958, 0, 961, 1080], fill=(60, 60, 60))
            im.save(os.path.join(d, f"standbild-{k}.png"), optimize=True)
    raw = sum(len(r.frames) for r in recs)
    from PIL import Image as _I
    import io as _io
    sz = [_I.open(_io.BytesIO(base64.b64decode(r.frames[0][1]))).size for r in recs]
    name += f" {'+'.join('%dx%d' % x for x in sz)}"
    print(f"SZENE {name}: {n} Bilder ({n / FPS:.1f} s, {FPS} fps), Quellbilder {raw}, Standbilder {k}", flush=True)
    return n


# =====================================================================================================
# Demo-Welt (englisch)
# =====================================================================================================
class DemoEN(HD.Demo):
    def api(self, u, method, path, body=None, raw=None, ctype=None, ok=(200, 201, 204)):
        import urllib.error
        hd = {"X-Saga": "1", "Cookie": "saga=" + self.ck(u), "Accept-Language": "en-US,en;q=0.9"}
        data = None
        if raw is not None:
            data, hd["Content-Type"] = raw, ctype
        elif body is not None:
            data, hd["Content-Type"] = json.dumps(body).encode(), "application/json"
        rq = urllib.request.Request(self.base + path, data=data, method=method, headers=hd)
        try:
            with urllib.request.urlopen(rq, timeout=60) as r:
                txt, code = r.read().decode("utf-8", "replace"), r.status
        except urllib.error.HTTPError as e:
            txt, code = e.read().decode("utf-8", "replace"), e.code
        if code not in ok:
            print(f"  API {method} {path} -> {code} {txt[:300]}", flush=True)
        try:
            return json.loads(txt) if txt else {}
        except ValueError:
            return {}


FAMILY = {"host": "saga.example", "title": "Sommer Family", "users": [
    ("anna", "Anna Sommer", True, ["family", "parents"], "AS", ((240, 128, 80), (232, 92, 112))),
    ("ben", "Ben Sommer", False, ["family", "parents"], "BS", ((70, 130, 210), (60, 90, 170))),
    ("clara", "Clara Sommer", False, ["family"], "CS", ((70, 170, 110), (40, 120, 90))),
    ("lena", "Grandma Lena", False, ["family"], "GL", ((190, 110, 200), (140, 80, 170)))]}

PARA = lambda i, t: {"id": i, "type": "paragraph", "data": {"text": t}}  # noqa: E731
HEAD = lambda i, t, lv=2: {"id": i, "type": "header", "data": {"text": t, "level": lv}}  # noqa: E731
CALL = lambda i, e, t: {"id": i, "type": "callout", "data": {"emoji": e, "text": t}}  # noqa: E731
CHECK = lambda i, items: {"id": i, "type": "list", "data": {"style": "checklist", "meta": {}, "items": [  # noqa: E731
    {"content": t, "meta": {"checked": c}, "items": []} for t, c in items]}}


def radicale_start(run, port):
    """CalDAV-Attrappe: Radicale des Systems (nur im Namensraum), Konto anna, Kalender "Family" mit Terminen heute/morgen."""
    st = os.path.join(os.environ.get("STATE_DIR", "/tmp"), "radicale")
    os.makedirs(st, exist_ok=True)
    pw = "cal-" + secrets.token_hex(8)
    with open(os.path.join(st, "users"), "w") as fh:
        fh.write(f"anna:{pw}\n")
    py = "/usr/bin/python3"                       # Radicale des Systems (Fedora: Modul fuer das System-Python)
    if subprocess.run([py, "-c", "import radicale"], capture_output=True).returncode:
        print("Radicale fehlt - Today ohne Termine", flush=True)
        return None
    conf = os.path.join(st, "config")
    with open(conf, "w") as fh:
        fh.write(f"[server]\nhosts = 127.0.0.1:{port}\n[auth]\ntype = htpasswd\nhtpasswd_filename = {st}/users\n"
                 f"htpasswd_encryption = plain\n[storage]\nfilesystem_folder = {st}/data\n[rights]\ntype = owner_only\n")
    log = open(os.path.join(st, "log"), "a")
    pr = subprocess.Popen([py, "-m", "radicale", "--config", conf], stdout=log, stderr=subprocess.STDOUT,
                          env=dict(os.environ, PATH="/usr/bin:/bin", PYTHONPATH="", HOME=st))
    run.procs = getattr(run, "procs", []) + [pr]
    import fakes
    fakes.forward("127.0.1.9", 80, port)
    base = "http://cal.saga.example/anna/family/"
    auth_h = "Basic " + base64.b64encode(f"anna:{pw}".encode()).decode()

    def dav(method, url, body=b"", ctype="application/xml"):
        rq = urllib.request.Request(url, data=body, method=method, headers={"Authorization": auth_h, "Content-Type": ctype})
        try:
            with urllib.request.urlopen(rq, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code
    import urllib.error
    for _ in range(60):
        try:
            if dav("PROPFIND", "http://cal.saga.example/anna/") in (207, 404, 401, 200):
                break
        except Exception:  # noqa: BLE001 - startet noch
            pass
        time.sleep(0.5)
    dav("MKCALENDAR", base, b'<?xml version="1.0"?><c:mkcalendar xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
        b'<d:set><d:prop><d:displayname>Family</d:displayname></d:prop></d:set></c:mkcalendar>')
    now = local_now()
    from zoneinfo import ZoneInfo

    def ev(uid, title, day, h, m, dur):
        s = now.replace(hour=h, minute=m, second=0, microsecond=0) + timedelta(days=day)
        e = s + timedelta(minutes=dur)
        f = lambda x: x.astimezone(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")  # noqa: E731
        ics = (f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//promo//EN\r\nBEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:{f(now)}\r\n"
               f"DTSTART:{f(s)}\r\nDTEND:{f(e)}\r\nSUMMARY:{title}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
        print("  Termin", title, dav("PUT", base + uid + ".ics", ics.encode(), "text/calendar"), flush=True)
    h = now.hour
    ev("e1", "Clara's swimming lesson", 0, min(h + 2, 22), 0, 60)
    ev("e2", "Dinner with Grandma Lena", 0, min(h + 4, 23), 30, 90)
    ev("e3", "Dentist – Clara", 1, 9, 30, 30)
    ev("e4", "Parents' evening", 1, 18, 0, 90)
    return {"base_url": "http://cal.saga.example/", "username": "anna", "password": pw}


def world(run, nur):
    dm = run.dm
    dm.users()
    today = local_now().date()
    ymd = lambda k: (today + timedelta(days=k)).isoformat()  # noqa: E731
    from saga import core
    REF = core.local_ref
    M = lambda u, n: f'<span class="hk-mention" data-u="{REF(u)}">{n}</span>'  # noqa: E731
    ids = {}
    allowed = ["cal.saga.example"]
    dm.setting("notes", dict(dm.auth.settings().get("notes") or {}, live=True))       # gleichzeitig bearbeiten (Szene 3)
    for i, (slug, name, url, cat) in enumerate((("photos", "Photos", "https://photos.saga.example", "Family"),
                                                ("cloud", "Nextcloud", "https://cloud.saga.example", "Family"),
                                                ("school", "School portal", "https://school.example.org", "Family"),
                                                ("movies", "Movie night", "https://movies.saga.example", "Leisure"),
                                                ("music", "Music", "https://music.saga.example", "Leisure"),
                                                ("home", "Smart home", "https://home.saga.example", "Home"))):
        dm.service(slug, name, url, i + 1, cat=cat)
    # Aufgaben (Today, rechte Leiste)
    for text, days, prio in (("Book the ferry", 1, 3), ("Birthday card for Grandma", 0, 2), ("Water the plants", 0, 1),
                             ("Return library books", -1, 2), ("Bike service", 4, 1), ("Plan Clara's party", 6, 2),
                             ("Renew car insurance", 9, 1)):
        dm.api("anna", "POST", "/api/tasks", {"text": text, "due": ymd(days), "prio": prio})
    # Family Wiki: Titelbild aus der eingebauten Galerie, Siegel als Symbol, Unterseiten-Galerie
    cov = dm.cover("anna", "bierstadt-lake-louise") or dm.cover("anna", "saga-kraeutergarten")
    wiki = dm.note("anna", "Family Wiki", icon="/siegel/smaragd/yggdrasil.svg", cover=cov)
    kids = [("Important numbers", "☎️", ("streifen", (214, 92, 80), (180, 60, 60))),
            ("Grandma's recipes", "🍰", ("punkte", (240, 170, 90), (220, 120, 70))),
            ("Summer holiday 2027", "🏖️", ("meer", (150, 200, 240), (90, 150, 210))),
            ("Cleaning rota", "🧹", ("streifen", (120, 170, 200), (80, 120, 170))),
            ("Birthdays", "🎂", ("punkte", (200, 130, 210), (150, 90, 180))),
            ("Garden", "🌻", ("blumen", (160, 210, 240), (200, 230, 245)))]
    for i, (t, ic, (mo, c1, c2)) in enumerate(kids):
        ids["wiki:" + t] = dm.note("anna", t, [PARA("p1", "Part of the Sommer family wiki.")], parent_id=wiki, icon=ic,
                                   cover=dm.art("anna", mo, c1, c2, f"cover-{i}"))
    dm.body("anna", wiki, [
        PARA("w1", "Everything the whole family needs to know – from emergency numbers to Grandma's favourite recipes."),
        PARA("w2", ""),
        {"id": "w3", "type": "widget", "data": {"v": 1, "kind": "subpages", "size": "m", "cfg": {"view": "gallery", "image": "cover"}}}])
    dm.share("anna", wiki, "family", "edit", "group")
    ids["wiki"] = wiki
    # Summer party (Szene 3, gemeinsam mit Ben)
    fest = dm.note("anna", "Summer party", icon="🎉")
    dm.body("anna", fest, [
        HEAD("s1", "Summer party in the garden"),
        PARA("s2", f"Saturday from 2 pm. {M('ben', 'Ben Sommer')} is in charge of the grill, Grandma Lena brings her apple pie."),
        CHECK("s3", [("Hang up the lanterns", True), ("Buy drinks", False), ("Charcoal", False), ("Borrow two garden tables", False)]),
        CALL("s4", "☀️", "If it rains, we party in the garage."),
        PARA("s5", "")])
    dm.share("anna", fest, "family", "edit", "group")
    ids["fest"] = fest
    # Haushalt + Chores-Board (Szene 4)
    home = dm.note("anna", "Household", [PARA("h1", "Shopping, chores and everything around the house.")], icon="🏠")
    dm.share("anna", home, "family", "edit", "group")
    chores, rows = dm.mkdb("anna", "Chores", "🧹", [
        {"id": "status", "name": "Status", "type": "select", "status": True, "options": [
            {"id": "todo", "name": "To do", "color": "gray", "group": "todo"}, {"id": "doing", "name": "Doing", "color": "blue", "group": "doing"},
            {"id": "done", "name": "Done", "color": "green", "group": "done"}]},
        {"id": "who", "name": "Who", "type": "person"}, {"id": "due", "name": "Due", "type": "date"},
        {"id": "room", "name": "Room", "type": "select", "options": [
            {"id": "kitchen", "name": "Kitchen", "color": "orange"}, {"id": "bath", "name": "Bathroom", "color": "blue"},
            {"id": "living", "name": "Living room", "color": "yellow"}, {"id": "garden", "name": "Garden", "color": "green"}]},
        {"id": "mins", "name": "Effort (min)", "type": "number"}],
        [{"id": "board", "name": "Board", "type": "board", "group_by": "status"},
         {"id": "table", "name": "Table", "type": "table"}],
        parent=home, rows=[{"title": t, "props": dict(status=st, who=[REF(u)], due=ymd(k), room=r, mins=a)} for t, st, u, k, r, a in (
            ("Clean the bathroom", "todo", "ben", 1, "bath", 40), ("Wipe out the fridge", "todo", "clara", 2, "kitchen", 20),
            ("Clean the oven", "todo", "anna", 4, "kitchen", 50), ("Water the tomatoes", "todo", "lena", 0, "garden", 10),
            ("Living room windows", "doing", "anna", 0, "living", 60),
            ("Mow the lawn", "done", "ben", -1, "garden", 45), ("Vacuum", "done", "clara", -1, "living", 25))])
    ids.update(home=home, chores=chores)
    dm.api("anna", "POST", "/api/notes", {"title": "Shopping list", "parent_id": home, "icon": "🛒"})
    # Weitere Seiten fuer die Seitenleiste
    ids["holiday"] = ids["wiki:Summer holiday 2027"]
    dm.body("anna", ids["holiday"], [
        HEAD("u1", "The plan"), PARA("u2", f"We leave on July 17. {M('ben', 'Ben Sommer')} books the ferry."),
        CALL("u3", "💡", "The holiday flat is paid – the rest is due by <b>June 15</b>."),
        HEAD("u4", "Packing list", 3),
        CHECK("u5", [("Swimwear and towels", True), ("Sun cream", True), ("Kite for the beach", False), ("Chargers and power bank", False)])])
    ids["notes"] = dm.note("anna", "Parents' evening notes", [HEAD("n1", "Parents' evening 7b"),
                                                              PARA("n2", "Topics: school trip, new maths teacher, phone rules.")], icon="📝")
    for t, ic, body in (("Bike tour to the coast", "🚲", "Start at the station, 42 km, lunch by the harbour."),
                        ("Grill recipes", "🔥", "Corn on the cob, halloumi skewers, Grandma's potato salad."),
                        ("Car maintenance", "🚗", "Tyres in October, inspection in March.")):
        dm.note("ben", t, [PARA("b1", body)], icon=ic)
    dm.setting("allowed_hosts", ", ".join(allowed))
    # Kalender (CalDAV-Attrappe) fuer Today
    if 1 in nur or 7 in nur:
        H0 = 19001 + 10 * int(os.environ.get("SLOT", "7"))
        try:
            acc = radicale_start(run, H0 + 9)
        except Exception:  # noqa: BLE001 - Today dann ohne Termine
            traceback.print_exc()
            acc = None
        if acc:
            time.sleep(4)                                   # Server sieht die freigegebenen Hosts (CACHE_TTL)
            for _ in range(5):
                d = dm.api("anna", "POST", "/api/settings/calendars/test", acc)
                cals = d.get("calendars") or []
                if cals:
                    break
                time.sleep(3)
            if cals:
                d = dm.api("anna", "POST", "/api/settings/calendars", dict(acc, label="Family", calendars=[
                    dict(c, show=True, color="#e8805c") for c in cals]))
                print("Kalender verbunden:", [c.get("name") for c in d.get("calendars", [])], flush=True)
    return ids


def set_english(run):
    from saga import core
    run.dm.setting("default_lang", "en")
    for u, *_ in FAMILY["users"]:
        core.set_user_lang(u, "en")


# =====================================================================================================
# Chats (Ende-zu-Ende ueber den Browser): Familiengruppe "Sommer Family 🏡"
# =====================================================================================================
def keys(run, w):
    """Chat-Schluessel einrichten bzw. freischalten (Schluessel bleibt im Speicher, erscheint in keinem Bild)."""
    w.p.goto(w.base + "/chats")
    w.p.wait_for_load_state("networkidle")
    w.p.wait_for_timeout(2500)
    p = w.p
    rk = run.__dict__.setdefault("rk", {})
    k = (w.base, w.user)
    if p.locator(".ct-setup-go").count():
        p.click(".ct-setup-go")
        p.wait_for_selector("#ct-rk")
        rk[k] = p.text_content("#ct-rk").strip()
        p.click(".hk-actions .btn-primary")
        p.fill(".ct-rk-last", rk[k][-4:])
        p.click(".ct-setup-done")
        p.wait_for_selector(".ct-setup-done", state="detached", timeout=60000)
    elif p.locator(".ct-rk-go").count() and rk.get(k):
        p.click(".ct-rk-go")
        p.fill(".ct-rk-in", rk[k])
        p.click(".ct-rk-ok")
        p.wait_for_selector(".ct-rk-ok", state="detached", timeout=60000)
    p.wait_for_timeout(800)
    SH.until(lambda: p.evaluate("() => !!(window.HKLive && HKLive.state().sse)"), 15000)


def chat_group(run):
    """Familiengruppe ueber die Oberflaeche (Name verschluesselt) und Verlauf; einmal je Lauf."""
    if getattr(run, "gid", None):
        return run.gid
    form = {1: CALL}.get(getattr(run, "scale", 1.5), FULL)
    ws = {u: P(run, u, form=form, media=MEDIA[u]) for u in ("anna", "ben", "clara", "lena")}
    for w in ws.values():
        keys(run, w)
    a = ws["anna"]
    a.p.click("#ct-new")
    a.p.wait_for_timeout(700)
    a.p.click(".ct-free-toggle")
    for n, q in (("Ben Sommer", "Ben"), ("Clara Sommer", "Clara"), ("Grandma Lena", "Lena")):
        a.p.fill(".ct-people-q", q)
        SH.until(lambda: a.p.locator(".ct-people-list .ct-person", has_text=n).count(), 8000)
        a.p.locator(".ct-people-list .ct-person", has_text=n).first.click()
        a.p.wait_for_timeout(300)
    a.p.fill(".ct-free input.hk-in", "Sommer Family 🏡")
    a.p.click(".ct-free-go")
    SH.until(lambda: a.p.locator("#ct-chat:not([hidden])").count(), 10000)
    a.p.wait_for_timeout(1500)
    gid = run.gid = a.p.evaluate("() => HKChat.state().cur")
    G = lambda w: w.go(f"/chats#c{gid}", 1800)  # noqa: E731
    G(a)
    SH.send_msg(a, "The invitations for the summer party are out – Saturday from 2 pm in the garden 🎉")
    b = ws["ben"]
    G(b)
    SH.send_msg(b, "Who's picking Grandma up from the station on Friday?")
    c = ws["clara"]
    G(c)
    SH.msg_menu(c, "Who's picking")
    c.p.locator("button.menu-item", has_text="Reply").first.click()
    SH.send_msg(c, "Me! 🙋 I'll cycle over after school.")
    w = ws["lena"]
    G(w)
    SH.send_msg(w, "Can't wait to see you all! I'm baking my apple pie 🍎")
    for w in ws.values():
        w.close()
    return gid


# =====================================================================================================
# Szenen
# =====================================================================================================
SCENES = {}


def scene(nr):
    def deco(fn):
        SCENES[nr] = fn
        return fn
    return deco


def side(w, text):
    return w.p.locator("#pg-mainnav a, #pg-mainnav button", has_text=text).first


@scene(1)
def s1_today(run):
    a = P(run, "anna")
    for k in ("chores", "fest", "holiday"):                 # "Pick up where you left off"
        a.go(f"/notes#{run.ids[k]}", 1500)
    a.go("/dashboard", 3000)
    a.quiet()
    a.p.mouse.move(a.mx, a.my)
    r = Rec(a, "1")
    r.start()
    a.wait(900)
    for t in ("Today", "Chats", "Tasks", "Events"):
        a.to(side(a, t), 0.3, 0.5, ms=520)
        a.wait(380)
    a.wait(300)
    a.to(a.p.locator("#hz-cards > *").first, 0.5, 0.5, ms=800)
    a.wait(700)
    r.still()
    cal = a.p.locator("#rail-cal-list > *").first
    if cal.count() and cal.is_visible():
        a.to(cal, 0.6, 0.5, ms=900)
        a.wait(900)
    task = a.p.locator("#rail-list li", has_text="Water the plants").first
    a.to(task, 0.5, 0.5, ms=700)
    a.wait(400)
    chk = task.locator("button, input[type=checkbox], .rail-check, .tk-check").first
    a.click(chk, ms=450)
    a.wait(1600)
    r.still()
    a.glide(a.mx - 260, a.my + 60, 900)
    a.wait(800)
    r.stop()
    write_scene("szene-1-heute", [r], run.out)
    a.close()


@scene(2)
def s2_page(run):
    a = P(run, "anna")
    a.go(f"/notes#{run.ids['wiki']}", 3000)
    a.p.wait_for_selector('.wg[data-kind="subpages"] img', timeout=15000)
    a.p.wait_for_timeout(1500)
    a.quiet()
    a.p.mouse.move(a.mx, a.my)
    r = Rec(a, "2")
    r.start()
    a.wait(900)
    r.still()
    empty = a.p.locator(".pm-doc p").nth(1)
    a.click(empty, 0.05, 0.5, ms=900)
    a.wait(450)
    a.typ("New this week: Clara starts swimming lessons on Tuesday 🏊")
    a.wait(350)
    a.p.keyboard.press("Enter")
    a.wait(300)
    a.typ("/", 8)
    a.wait(500)
    a.typ("check", 9)
    a.wait(700)
    a.p.keyboard.press("Enter")
    a.wait(450)
    a.typ("Pack the swim bag")
    a.p.keyboard.press("Enter")
    a.wait(250)
    a.typ("Buy new goggles")
    a.wait(600)
    a.glide(a.mx + 120, a.my + 160, 900)
    a.wait(900)
    r.still()
    a.wait(600)
    r.stop()
    write_scene("szene-2-seite", [r], run.out)
    a.close()


def _type_both(a, b, ta, tb, cps=11):
    """Beide tippen abwechselnd Zeichen fuer Zeichen (so erscheinen die Aenderungen gleichzeitig)."""
    n = max(len(ta), len(tb))
    for i in range(n):
        if i < len(ta):
            a.p.keyboard.type(ta[i])
        if i < len(tb):
            b.p.keyboard.type(tb[i])
        a.p.wait_for_timeout(int(1000 / cps * random.uniform(0.7, 1.3)))


@scene(3)
def s3_live(run):
    a, b = P(run, "anna", form=HALF), P(run, "ben", form=HALF)
    for w in (a, b):
        w.go(f"/notes#{run.ids['fest']}", 3500)
        w.quiet()
        w.p.mouse.move(w.mx, w.my)
    ra, rb = Rec(a, "3a"), Rec(b, "3b")
    ra.start()
    rb.start()
    a.wait(800)
    # Anna: ans Ende des Absatzes; Ben: in den leeren Absatz am Ende
    pa = a.p.locator(".pm-doc p", has_text="apple pie").first
    a.click(pa, 0.995, 0.5, dx=-2)
    a.p.keyboard.press("End")
    pb = b.p.locator(".pm-doc p").last
    b.click(pb, 0.05, 0.5)
    a.wait(600)
    _type_both(a, b, " Clara makes the playlist 🎶", "Ben: bring the big speaker 🔊")
    a.wait(700)
    ra.still()
    rb.still()
    # Ben hakt "Buy drinks" ab, Anna sieht es
    item = b.p.locator(".pm-doc li", has_text="Buy drinks").first
    b.click(item.locator(".pm-check").first, ms=700)
    a.wait(1500)
    ra.still()
    rb.still()
    a.wait(700)
    ra.stop()
    rb.stop()
    write_scene("szene-3-live", [ra, rb], run.out)
    a.close()
    b.close()


@scene(4)
def s4_db(run):
    a = P(run, "anna")
    a.go(f"/notes#{run.ids['chores']}", 3000)
    a.quiet()
    a.p.mouse.move(a.mx, a.my)
    r = Rec(a, "4")
    r.start()
    a.wait(900)
    card = a.p.locator(".board-card", has_text="Clean the bathroom").first
    cols = a.p.locator(".board-col")
    target = cols.nth(1)
    a.to(card, 0.4, 0.5, ms=900)
    a.wait(300)
    a.p.mouse.down()
    a.wait(120)
    x, y = a.point(target.locator(".board-cards"), 0.4, 0.0, dy=60)
    a.glide(a.mx + 20, a.my + 10, 200)
    a.glide(x, y, 1300)
    a.wait(250)
    a.p.mouse.up()
    a.wait(1300)
    r.still()
    # Eigenschaft aendern: Karte oeffnen, Raum bzw. Person umstellen
    card = a.p.locator(".board-card", has_text="Wipe out the fridge").first
    a.click(card, 0.4, 0.4, ms=900)
    a.wait(1500)
    try:
        pk = a.p.frame_locator("#peek-frame")             # Seitenansicht der Zeile = eingebettete Seite
        val = pk.locator('.val-btn[aria-label^="Status"]').first
        a.click(val, 0.3, 0.5, ms=800)
        a.wait(700)
        opt = pk.locator(".menu-item.opt-item", has_text="Doing").first
        a.click(opt, 0.3, 0.5, ms=600)
        a.wait(1000)
        val = pk.locator('.val-btn[aria-label^="Who"]').first
        a.to(val, 0.3, 0.5, ms=700)
        a.wait(800)
    except Exception as e:  # noqa: BLE001
        print("  Szene 4: Eigenschaft nicht geaendert:", str(e)[:200], flush=True)
    r.still()
    a.wait(900)
    a.p.keyboard.press("Escape")
    a.wait(1200)
    r.stop()
    write_scene("szene-4-datenbank", [r], run.out)
    a.close()


@scene(5)
def s5_chat(run):
    gid = chat_group(run)
    b, c = P(run, "ben", media=MEDIA["ben"]), P(run, "clara", media=MEDIA["clara"], voice=True)
    for w in (b, c):
        keys(run, w)
        w.go(f"/chats#c{gid}", 1800)
    a = P(run, "anna", media=MEDIA["anna"])
    keys(run, a)
    a.go(f"/chats#c{gid}", 3000)
    a.quiet()
    a.p.mouse.move(a.mx, a.my)
    r = Rec(a, "5")
    r.start()
    a.wait(800)
    SH.send_msg(b, "The grill is clean – Saturday can come 🔥")
    a.wait(1400)
    msg = a.p.locator(".ct-msg", has_text="The grill is clean").last
    a.to(msg, 0.3, 0.5, ms=800)
    a.wait(400)
    act = msg.locator(".ct-act:not(.ct-react-b)")
    a.click(act, ms=400)
    a.wait(500)
    heart = a.p.locator(".ct-rq-b", has_text="❤️").first
    a.click(heart, ms=500)
    a.wait(900)
    a.click(a.p.locator("#ct-input"), 0.3, 0.5, ms=800)
    a.typ("You're the best! I'll bring the lanterns 🏮")
    a.wait(300)
    a.p.keyboard.press("Enter")
    a.wait(900)
    r.still()
    # Clara nimmt eine Sprachnachricht auf
    c.p.click("#ct-mic")
    c.p.wait_for_selector(".ct-rec-stop", timeout=10000)
    a.wait(1500)
    c.p.click(".ct-rec-stop")
    c.p.wait_for_selector(".ct-rec-send", timeout=10000)
    c.p.click(".ct-rec-send")
    a.wait(2200)
    voice = a.p.locator(".ct-msg").last
    a.to(voice, 0.25, 0.5, ms=900)
    a.wait(500)
    play = voice.locator("button").first
    if play.count():
        a.click(play, ms=400)
    a.wait(2200)
    r.still()
    a.wait(600)
    r.stop()
    write_scene("szene-5-chat", [r], run.out)
    for w in (a, b, c):
        w.close()


@scene(6)
def s6_call(run):
    gid = chat_group(run)
    b = P(run, "ben", form=CALL, media=MEDIA["ben"], voice=True)
    c = P(run, "clara", form=CALL, media=MEDIA["clara"])
    for w in (b, c):
        keys(run, w)
        w.go("/chats", 1500)
    a = P(run, "anna", form=CALL, media=MEDIA["anna"])
    keys(run, a)
    a.go(f"/chats#c{gid}", 2500)
    a.p.wait_for_selector("#ct-chat:not([hidden])")
    SH.until(lambda: a.p.locator("#ct-call-v").is_visible(), 10000)
    a.quiet()
    a.p.mouse.move(a.mx, a.my)
    r = Rec(a, "6")
    r.start()
    a.wait(700)
    a.click(a.p.locator("#ct-call-v"), ms=900)
    for w in (b, c):
        SH.accept(w)
    SH.until(lambda: len(set((SH.room(a) or {}).get("legs") or [])) >= 3, 30000)
    a.glide(1250, 760, 1100)
    a.wait(2000)
    r.still()
    # Knoten-/Verbindungsanzeige oben kurz (TCP = ueber den eingebauten Knoten), solange die Leiste ganz zu sehen ist
    chip = a.p.locator(".cl-top, .cl-head, body").locator("text=TCP").first
    if chip.count() and chip.is_visible():
        a.to(chip, ms=900)
        a.wait(1600)
        r.still()
    # Sprecher-Ansicht: Layout-Menue in der Bedienleiste
    lay = a.p.locator(".cl-ctl .cl-layout").first
    if lay.count() and lay.is_visible():
        a.click(lay, ms=900)
        a.wait(600)
        a.click(a.p.locator(".cl-lmenu .cl-lmenu-i", has_text="Speaker").first, ms=600)
        a.wait(2200)
    else:
        print("  Szene 6: kein Layout-Knopf", flush=True)
    # Ben teilt den Bildschirm
    SH.ctl(b, "Share screen").click()
    if SH.until(lambda: b.p.locator(".cl-hint .cl-hint-go").count(), 3000):
        b.p.click(".cl-hint .cl-hint-go")
    a.wait(4500)
    r.still()
    a.glide(1880, 1040, 1000)
    a.wait(1200)
    r.stop()
    write_scene("szene-6-anruf", [r], run.out)
    SH.hangup(c, b, a)
    a.wait(1500)
    for w in (a, b, c):
        w.close()


@scene(7)
def s7_dark_phone(run):
    a = P(run, "anna", dark=True)
    a.go(f"/notes#{run.ids['wiki']}", 3000)
    a.p.wait_for_selector('.wg[data-kind="subpages"] img', timeout=15000)
    a.p.wait_for_timeout(1200)
    a.quiet()
    a.p.mouse.move(a.mx, a.my)
    r = Rec(a, "7")
    r.start()
    a.wait(900)
    r.still()
    card = a.p.locator('.wg[data-kind="subpages"] a, .wg[data-kind="subpages"] .wg-card', has_text="Summer holiday").first
    a.click(card, ms=1100)
    a.wait(2200)
    kite = a.p.locator(".pm-doc li", has_text="Kite for the beach").first
    a.click(kite.locator(".pm-check").first, ms=1000)
    a.wait(900)
    a.glide(a.mx + 260, a.my + 140, 900)
    a.wait(700)
    r.still()
    a.wait(500)
    r.stop()
    write_scene("szene-7-dunkel", [r], run.out)
    a.close()
    # Handy (dunkel): Today -> Chats -> Familiengruppe, kurze Antwort
    chat_group(run)
    h = P(run, "anna", dark=True, form=PHONE, media=MEDIA["anna"])
    keys(run, h)
    h.go("/dashboard", 3000)
    h.quiet()
    r = Rec(h, "7h")
    r.start()
    h.wait(1200)
    r.still()
    tab = h.p.locator("#mob-tabs a, #mob-tabs button", has_text="Chats").first
    h.tap(tab)
    h.wait(2200)
    it = h.p.locator(".ct-item-b", has_text="Sommer Family").first
    h.tap(it)
    h.wait(2000)
    h.tap(h.p.locator("#ct-input"))
    h.wait(500)
    h.typ("On my way home 🚲")
    h.wait(300)
    h.p.keyboard.press("Enter")
    h.wait(1500)
    r.still()
    h.wait(500)
    r.stop()
    write_scene("szene-7-handy", [r], run.out)
    h.close()


def verein_keys(run, w):
    keys(run, w)


@scene(8)
def s8_fed(run):
    vs = run.verein
    b = P(run, "ben")
    keys(run, b)
    f = P(run, None, base="http://verein.example")
    f.user = "frieda"
    f.p.goto("http://verein.example/login")
    f.p.fill("input[name=username]", "frieda")
    f.p.fill("input[name=password]", run.dm.pw)
    f.p.click("button[type=submit]")
    f.p.wait_for_load_state("networkidle")
    keys(run, f)
    # Vorlauf (nicht gefilmt): Ben schreibt Frieda, Frieda nimmt an und antwortet
    b.p.click("#ct-new")
    b.p.fill(".ct-people-q", "Frieda")
    b.p.wait_for_selector(".ct-person.ct-remote", timeout=15000)
    b.p.click(".ct-person.ct-remote")
    b.p.wait_for_selector("#ct-chat:not([hidden])", timeout=15000)
    SH.send_msg(b, "Hi Frieda! Anna and I would love a plot at Lindenhof next year 🌱")
    cid = b.p.evaluate("() => HKChat.state().cur")
    f.p.goto("http://verein.example/chats")
    f.p.wait_for_load_state("networkidle")
    SH.until(lambda: f.p.locator("#ct-req-h").count(), 20000)
    f.p.locator(".ct-item-b").first.click()
    f.p.wait_for_selector(".ct-req-accept", timeout=15000)
    f.p.click(".ct-req-accept")
    SH.until(lambda: f.p.is_visible("#ct-compose"), 10000)
    SH.send_msg(f, "Hello Ben! Plot 4 is free from April – come and have a look on Saturday?")
    b.go("/chats", 2500)
    b.quiet()
    b.p.mouse.move(b.mx, b.my)
    r = Rec(b, "8")
    r.start()
    b.wait(800)
    it = b.p.locator(".ct-item-b", has_text="Frieda").first
    b.click(it, 0.4, 0.5, ms=900)
    b.wait(1500)
    sub = b.p.locator("#ct-sub")
    b.to(b.p.locator("#ct-title"), 0.5, 0.5, ms=800)
    b.wait(900)
    r.still()
    b.click(b.p.locator("#ct-input"), 0.3, 0.5, ms=900)
    b.typ("Perfect, we'll come by with the kids! 🌻")
    b.p.keyboard.press("Enter")
    b.wait(1200)
    SH.send_msg(f, "Wonderful – see you at 10 at the gate 🌷")
    b.wait(2500)
    r.still()
    b.wait(600)
    r.stop()
    write_scene("szene-8-foederation", [r], run.out)
    print("  Szene 8: Kopfzeile:", sub.inner_text()[:120] if sub.count() else "-", "chat", cid, flush=True)
    b.close()
    f.close()
    _ = vs


@scene(9)
def s9_admin(run):
    w = P(run, None, base=run.adm)
    w.user = "anna"
    w.p.goto(run.adm + "/login")
    w.p.fill("input[name=username]", "anna")
    w.p.fill("input[name=password]", run.dm.pw)
    w.p.click("button[type=submit]")
    w.p.wait_for_load_state("networkidle")
    w.go("/calls", 2500)
    w.p.mouse.move(w.mx, w.my)
    r = Rec(w, "9")
    r.start()
    w.wait(900)
    card = w.p.locator("section#horn")
    w.to(card, 0.3, 0.1, ms=900)
    w.wait(300)
    top = card.bounding_box()["y"]
    w.scroll(max(0, top - 80), 1400)
    w.wait(900)
    r.still()
    box = w.p.locator(".horn-tunnel")
    if box.count() and box.is_visible():
        w.to(box, 0.3, 0.3, ms=900)
        b = box.locator(".tu-btn")
        if b.count() and b.is_visible():
            w.click(b, ms=600)
            SH.until(lambda: "✓" in box.locator(".tu-check").inner_text() or "✗" in box.locator(".tu-check").inner_text(), 15000)
        w.wait(1500)
        print("  Szene 9: Tunnel-Kasten:", box.inner_text()[:300].replace("\n", " | "), flush=True)
        r.still()
    w.wait(800)
    r.stop()
    print("  Szene 9: Knoten-Karte:", card.inner_text()[:400].replace("\n", " | "), flush=True)
    write_scene("szene-9-admin", [r], run.out)
    w.close()


# =====================================================================================================
def innen(nur):
    sys.path.insert(0, ROOT)
    os.environ["BASE_URL"] = "http://saga.example"
    from playwright.sync_api import sync_playwright
    import fakes
    run = SH.Run("familie", "knoten")
    run.nur = nur
    run.out = os.environ.get("PROMO_OUT") or os.path.join(os.environ.get("SHOTS", "/tmp"), "promo")
    os.makedirs(run.out, exist_ok=True)
    P0, AP = int(os.environ["PORT"]), int(os.environ["ADMIN_PORT"])
    fakes.forward("127.0.1.1", 80, P0)
    fakes.forward("127.0.1.2", 80, AP)
    run.p2, run.ap2 = P0 + 6, P0 + 7                          # verein.example (Szene 8)
    fakes.forward("127.0.1.7", 80, run.p2)
    fakes.forward("127.0.1.8", 80, run.ap2)
    run.dm = DemoEN("familie", base=run.base)
    run.dm.W = FAMILY
    print("Zeitzone der Browser:", TZ, local_now().strftime("%Y-%m-%d %H:%M"), flush=True)
    fail = []
    with sync_playwright() as pw:
        env = dict(os.environ, LANG="en_US.UTF-8", LC_ALL="en_US.UTF-8", TZ=TZ)
        fonts = os.environ.get("HB_FONTS")
        if fonts and os.path.isdir(fonts):
            conf = os.path.join(fonts, "fonts.conf")
            with open(conf, "w") as fh:
                fh.write('<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
                         f'<include ignore_missing="yes">/etc/fonts/fonts.conf</include><dir>{fonts}</dir>'
                         f'<cachedir>{fonts}/cache</cachedir></fontconfig>')
            env["FONTCONFIG_FILE"] = conf
        origins = ",".join("http://" + h for h in ("saga.example", "admin.saga.example", "verein.example", "admin.verein.example"))

        def use(scale):
            """Browser mit passendem Geraetefaktor (hoechstens einer offen; offene Kontexte vorher schliessen)."""
            if getattr(run, "b", None) and run.scale == scale:
                return
            if getattr(run, "b", None):
                for w in list(run.open):
                    w.close()
                run.b.close()
            run.scale = scale
            run.b = pw.chromium.launch(args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                                             "--autoplay-policy=no-user-gesture-required", "--hide-scrollbars",
                                             f"--force-device-scale-factor={scale}",
                                             f"--unsafely-treat-insecure-origin-as-secure={origins}"],
                                       env=env, channel=os.environ.get("PW_CHANNEL") or None)
        run.use = use
        use(FULL["device_scale_factor"])
        run.ids = world(run, nur)
        run.dm.save()
        if 8 in nur:                                          # zweiter Server + Foederation (Admin-Oberflaeche, deutsch)
            try:
                run.verein = SH.verein_server(run)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                fail.append(8)
            run.dm.setting("allowed_hosts", "cal.saga.example, verein.example")
        set_english(run)
        run.dm.setting("title", FAMILY["title"])
        t0 = time.time()
        while time.time() - t0 < 60:                          # Server zeigt Titel und Sprache (CACHE_TTL)
            try:
                with urllib.request.urlopen(run.base + "/login", timeout=10) as rr:
                    if FAMILY["title"] in rr.read().decode("utf-8", "replace"):
                        break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(2)
        time.sleep(4)
        print("Demo bereit:", json.dumps(run.ids), flush=True)
        for nr in nur:
            if nr in fail:
                continue
            t1 = time.time()
            try:
                SCENES[nr](run)
                print(f"OK  {nr} {NAMES[nr]} ({time.time() - t1:.0f} s)", flush=True)
            except Exception:  # noqa: BLE001
                fail.append(nr)
                print(f"FAIL {nr} {NAMES[nr]}", flush=True)
                traceback.print_exc()
                for i, w in enumerate(list(run.open)):
                    try:
                        w.p.screenshot(path=os.path.join(run.out, f"x-fehler-{nr}-{i}-{w.user}.png"))
                    except Exception:  # noqa: BLE001
                        pass
                    SH.hangup(w)
                time.sleep(2)
                for w in list(run.open):
                    w.close()
        run.b.close()
    for pr in getattr(run, "procs", []):
        pr.terminate()
        try:
            pr.wait(20)
        except Exception:  # noqa: BLE001
            pr.kill()
    if getattr(run, "verein_db", None):
        import psycopg
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
            c.execute(f"DROP DATABASE IF EXISTS {run.verein_db} WITH (FORCE)")
    print("Szenen:", ",".join(map(str, nur)), "Fehler:", ",".join(map(str, fail)) or "-", "Ordner:", run.out, flush=True)
    return 1 if fail else 0


def kontakt(root):
    """Pruefbogen je Szene: 12 gleichmaessig verteilte Bilder verkleinert in einem Raster (kontakt-<szene>.jpg)."""
    from PIL import Image, ImageDraw
    for d in sorted(os.listdir(root)):
        fr = sorted(f for f in os.listdir(os.path.join(root, d)) if f.endswith(".jpg")) if d.startswith("szene-") else []
        if not fr:
            continue
        pick = [fr[min(len(fr) - 1, int(i * (len(fr) - 1) / 11))] for i in range(12)]
        im0 = Image.open(os.path.join(root, d, pick[0]))
        tw = 480
        th = int(tw * im0.height / im0.width)
        cols = 4 if im0.width >= im0.height else 6
        rows = (12 + cols - 1) // cols
        sheet = Image.new("RGB", (cols * tw, rows * (th + 18)), (30, 30, 30))
        dr = ImageDraw.Draw(sheet)
        for i, f in enumerate(pick):
            x, y = (i % cols) * tw, (i // cols) * (th + 18)
            sheet.paste(Image.open(os.path.join(root, d, f)).convert("RGB").resize((tw, th)), (x, y + 18))
            dr.text((x + 4, y + 3), f"{f}  {int(f[:5]) / FPS:.1f} s", fill=(230, 230, 230))
        sheet.save(os.path.join(root, f"kontakt-{d}.jpg"), quality=85)
        print("Pruefbogen", d, len(fr), "Bilder", flush=True)


if __name__ == "__main__":
    if "--kontakt" in sys.argv:
        kontakt(sys.argv[sys.argv.index("--kontakt") + 1])
        sys.exit(0)
    if "--liste" in sys.argv:
        for k, v in NAMES.items():
            print(k, v)
        sys.exit(0)
    N = nur_arg(sys.argv)
    sys.exit(innen(N) if os.environ.get("PROMO_INNEN") == "1" and os.environ.get("PORT") else aussen(N))
