"""Demo-Welt fuer die Handbuch-Bilder (Saga 2.5): legt Konten, Profilbilder, Seiten, Datenbanken, Aufgaben, Dienste und
Verbindungen per DB (Konten, Einstellungen) und HTTP-API (alles andere, wie ein Browser) an. Nur erfundene Inhalte.

Welten (docs/handbuch/README.md):
  familie  Familie Sommer auf saga.example: anna (Admin, Mutter), ben, clara, lena (Oma)
  verein   Gartenverein Lindenhof auf verein.example: admin, frieda (Admin, Vorsitzende), paul (Kassenwart), greta

Laeuft normalerweise aus tools/screens-handbuch.py (im Netz-Namensraum der Testumgebung, Attrappen aus
tools/handbuch-fakes/fakes.py). Allein: im Saga-Ordner mit derselben Umgebung wie der Server (DATABASE_URL, SECRET_KEY,
HB_BASE = Adresse des Servers):  PYTHONPATH=tests/pgshim:. python tools/handbuch-demo.py familie|verein
Das Passwort aller Demo-Konten kommt aus HB_PW (sonst zufaellig, steht dann in $STATE_DIR/handbuch-demo.json)."""
import base64
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "handbuch-fakes"))
import fakes  # noqa: E402

WELTEN = {
    "familie": {"host": "saga.example", "title": "Familie Sommer", "users": [
        # Anmeldename, Anzeigename, Admin, Gruppen, Initialen, Farben
        ("anna", "Anna Sommer", True, ["familie", "eltern"], "AS", ((240, 128, 80), (232, 92, 112))),
        ("ben", "Ben Sommer", False, ["familie", "eltern"], "BS", ((70, 130, 210), (60, 90, 170))),
        ("clara", "Clara Sommer", False, ["familie"], "CS", ((70, 170, 110), (40, 120, 90))),
        ("lena", "Oma Lena", False, ["familie"], "OL", ((190, 110, 200), (140, 80, 170)))]},
    "verein": {"host": "verein.example", "title": "Gartenverein Lindenhof", "users": [
        ("admin", "Admin", True, [], "AD", ((120, 120, 120), (80, 80, 80))),
        ("frieda", "Frieda Linde", True, ["vorstand", "mitglieder"], "FL", ((90, 150, 80), (50, 110, 60))),
        ("paul", "Paul Brandt", False, ["vorstand", "mitglieder"], "PB", ((210, 150, 60), (180, 100, 40))),
        ("greta", "Greta Ufer", False, ["mitglieder"], "GU", ((80, 150, 190), (50, 100, 150)))]},
}
# Anzeige-Hosts der Attrappen (UMG_HOSTS in tools/handbuch-stack.sh zeigt sie auf 127.0.1.x)
NC_HOST, BO_HOST = "cloud.saga.example", "bookorbit.example"


def ymd(d):
    return d.isoformat()


class Demo:
    def __init__(self, welt, base=None, pw=None):
        import auth   # noqa: E402 - erst hier: braucht DATABASE_URL/SECRET_KEY des Laufs
        import db     # noqa: E402
        self.auth, self.db = auth, db
        self.welt, self.W = welt, WELTEN[welt]
        self.base = (base or os.environ.get("HB_BASE") or "http://" + self.W["host"]).rstrip("/")
        self.pw = pw or os.environ.get("HB_PW") or "Demo-" + secrets.token_hex(6) + "-Pw1"
        self.ids = {}

    # --- Zugang ------------------------------------------------------------------------------------------------------
    def ck(self, u):
        from itsdangerous import TimestampSigner
        i = self.auth.user_info(u, fresh=True)
        d = {"uid": u, "name": i["name"], "pwc": i["epoch"] or 0}
        return TimestampSigner(self.auth.SECRET).sign(base64.b64encode(json.dumps(d).encode())).decode()

    def api(self, u, method, path, body=None, raw=None, ctype=None, ok=(200, 201, 204)):
        hd = {"X-Saga": "1", "Cookie": "saga=" + self.ck(u)}
        data = None
        if raw is not None:
            data, hd["Content-Type"] = raw, ctype
        elif body is not None:
            data, hd["Content-Type"] = json.dumps(body).encode(), "application/json"
        rq = urllib.request.Request(self.base + path, data=data, method=method, headers=hd)
        try:
            with urllib.request.urlopen(rq, timeout=60) as r:
                txt = r.read().decode("utf-8", "replace")
                code = r.status
        except urllib.error.HTTPError as e:
            txt, code = e.read().decode("utf-8", "replace"), e.code
        if code not in ok:
            print(f"  API {method} {path} -> {code} {txt[:300]}", flush=True)
        try:
            return json.loads(txt) if txt else {}
        except ValueError:
            return {}

    # --- Bausteine ---------------------------------------------------------------------------------------------------
    def users(self):
        self.db.wait_ready()
        self.db.init_schema()
        self.auth.save_setting("title", self.W["title"])
        for u, name, adm, groups, *_ in self.W["users"]:
            if not self.auth.get_user(u):
                self.auth.create_user(u, self.pw, name, email=f"{u}@{self.W['host']}", is_admin=adm, groups=groups)
            else:
                self.auth.set_password(u, self.pw)
        for u, name, adm, groups, ini, col in self.W["users"]:
            if u != "admin":
                self.api(u, "PUT", "/api/account/avatar", raw=fakes.avatar(ini, *col), ctype="image/png")

    def setting(self, k, v):
        self.auth.save_setting(k, v)

    def note(self, u, title, blocks=None, **kw):
        d = self.api(u, "POST", "/api/notes", dict(title=title, **kw))
        nid = d.get("id") or (d.get("note") or {}).get("id")
        if blocks is not None and nid:
            self.api(u, "PATCH", f"/api/notes/{nid}", {"format": "blocks", "body": json.dumps({"blocks": blocks}, ensure_ascii=False)})
        return nid

    def upload(self, u, name, data, mime="image/png"):
        """Datei hochladen wie der Editor (multipart, Feld "image") -> URL /files/..."""
        b = "----hb" + secrets.token_hex(8)
        body = (f"--{b}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\nContent-Type: {mime}\r\n\r\n").encode() + \
            data + f"\r\n--{b}--\r\n".encode()
        d = self.api(u, "POST", "/api/files", raw=body, ctype="multipart/form-data; boundary=" + b)
        return ((d.get("file") or {}).get("url")) or d.get("url")

    def art(self, u, motif, c1, c2, name):
        """Gezeichnetes Titelbild hochladen (eigene Datei = erlaubtes Titelbild)."""
        return self.upload(u, name + ".png", fakes.cover_art(motif, c1, c2))

    def cover(self, u, name):
        """Titelbild aus der eingebauten Sammlung (static/covers) nach Dateiname; None = ohne Titelbild."""
        if not hasattr(self, "_covers"):
            self._covers = [c["url"] for c in (self.api(u, "GET", "/api/covers").get("covers") or [])]
        return next((c for c in self._covers if name and name in c), None)

    def service(self, slug, name, url, sort):
        import auth
        with self.db.connect() as c, c:
            if c.execute("SELECT 1 FROM service WHERE slug=?", (slug,)).fetchone():
                return
            c.execute("INSERT INTO service (slug, name, url, icon, category, extra_groups, everyone, check_status, sort, created) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?)", (slug, name, url, "", self.W["title"].split()[0], "", 1, 0, sort, time.time()))
            auth.ensure_group(c, "s_" + slug, f"Zugang zu {name}")

    # --- Welt Familie --------------------------------------------------------------------------------------------------
    def familie(self, nc_login=None, nc_pw=None, nc_uid=None, nc_ids=None):
        today = date.today()
        self.users()
        self.setting("allowed_hosts", ", ".join([NC_HOST, BO_HOST]))   # nur die Anzeige-Hosts der Attrappen (steht in Bildern)
        self.service("eltern", "Elternportal", "https://eltern.example.org", 1)
        self.service("fotos", "Fotos", "https://fotos.saga.example", 2)
        self.service("cloud", "Nextcloud", "https://cloud.saga.example", 3)
        for text, days, prio in (("Fähre buchen", 1, 3), ("Geburtstagskarte für Oma Lena", 0, 2), ("Blumen gießen", 0, 1),
                                 ("Elternabend vorbereiten", 3, 2), ("Fahrräder zur Inspektion", 6, 1), ("Steuererklärung abgeben", -2, 3)):
            self.api("anna", "POST", "/api/tasks", {"text": text, "due": ymd(today + timedelta(days=days)), "prio": prio})
        P = lambda i, t: {"id": i, "type": "paragraph", "data": {"text": t}}  # noqa: E731
        H = lambda i, t, lv=2: {"id": i, "type": "header", "data": {"text": t, "level": lv}}  # noqa: E731
        for name, url in (("Vertretungsplan", "https://schule.example.org/vertretungsplan"), ("Mensa", "https://mensa.example.org")):
            self.api("clara", "POST", "/api/bookmarks", {"name": name, "url": url, "category": "Schule"})
        # Familien-Wiki mit Unterseiten (Titelbilder aus der eingebauten Sammlung)
        # Titelbilder: gezeichnete, flache Motive (eigene Uploads) - klein als PNG, keine Fotos
        wiki = self.note("anna", "Familien-Wiki", icon="📖", cover=self.art("anna", "haus", (238, 214, 170), (214, 170, 120), "wiki"))
        kids = [("Wichtige Nummern", "☎️", ("streifen", (214, 92, 80), (180, 60, 60))),
                ("Rezepte von Oma Lena", "🍰", ("punkte", (240, 170, 90), (220, 120, 70))),
                ("Urlaub Ostsee 2027", "🏖️", ("meer", (150, 200, 240), (90, 150, 210))),
                ("Putzplan", "🧹", ("streifen", (120, 170, 200), (80, 120, 170))),
                ("Geburtstage", "🎂", ("punkte", (200, 130, 210), (150, 90, 180))),
                ("Garten", "🌻", ("blumen", (160, 210, 240), (200, 230, 245)))]
        for i, (t, ic, (mo, c1, c2)) in enumerate(kids):
            nid = self.note("anna", t, [P("p1", "Gehört zum Familien-Wiki der Sommers.")], parent_id=wiki, icon=ic,
                            cover=self.art("anna", mo, c1, c2, f"titel-{i}"))
            self.ids["wiki:" + t] = nid
        self.api("anna", "PATCH", f"/api/notes/{wiki}", {"format": "blocks", "body": json.dumps({"blocks": [
            P("w1", "Alles, was die ganze Familie wissen muss – von der Notfallnummer bis zum Lieblingsrezept."),
            {"id": "w2", "type": "widget", "data": {"v": 1, "kind": "subpages", "size": "m", "cfg": {"view": "gallery", "image": "cover"}}},
            P("w3", "Neue Seiten legst du einfach unter „Familien-Wiki“ an – sie erscheinen hier von selbst.")]}, ensure_ascii=False)})
        self.ids["wiki"] = wiki
        # Gewohnheiten (Datenbank) + Seite "Unser Jahr" mit vier Widgets in einer Reihe
        hdb = self.note("anna", "Gewohnheiten", kind="database", icon="✅", cover=None)
        self.api("anna", "PATCH", f"/api/notes/{hdb}", {"schema": {"props": [
            {"id": "datum", "name": "Datum", "type": "date"}, {"id": "sport", "name": "Sport", "type": "checkbox"},
            {"id": "lesen", "name": "Lesen", "type": "checkbox"}, {"id": "wasser", "name": "Wasser trinken", "type": "checkbox"}],
            "views": [{"id": "alle", "name": "Alle", "type": "table"}]}})
        for i in range(0, 7):
            d = today - timedelta(days=i)
            self.api("anna", "POST", "/api/notes", {"title": d.strftime("%d.%m."), "parent_id": hdb,
                                                    "props": {"datum": ymd(d), "sport": i in (0, 2, 3, 5), "lesen": i != 4, "wasser": i in (0, 1, 2, 4, 5, 6)}})
        W = lambda i, kind, cfg, **ex: {"id": i, "type": "widget", "data": dict({"v": 1, "kind": kind, "cfg": cfg}, **ex)}  # noqa: E731
        year = today.year + (1 if today.month >= 8 else 0)
        jahr = self.note("anna", "Unser Jahr", icon="🗓️", cover=None)
        self.api("anna", "PATCH", f"/api/notes/{jahr}", {"format": "blocks", "body": json.dumps({"blocks": [
            P("j1", "Unsere Ziele und Termine auf einen Blick."),
            {"id": "j2", "type": "columns", "data": {"cols": [
                {"blocks": [W("c1", "countdown", {"date": f"{year}-07-24", "dir": "until", "unit": "days"}, title="Bis zum Urlaub")]},
                {"blocks": [W("c2", "progress", {"src": "fixed", "value": 1350, "max": 2000}, title="Urlaubskasse")]},
                {"blocks": [W("c3", "metric", {"src": "tasks", "agg": "open", "fmt": "num"}, title="Offene Aufgaben")]},
                {"blocks": [W("c4", "habit", {"db": hdb, "date": "datum", "habits": ["sport", "lesen", "wasser"], "range": 7}, title="Gewohnheiten")]}]}},
            P("j3", "")]}, ensure_ascii=False)})
        self.ids.update(habits=hdb, jahr=jahr)
        # Nextcloud: Verbindung (App-Passwort der Attrappe) + Seite mit Datei als Verweis
        if nc_login:
            from saga import nextcloud
            nextcloud._save_account("anna", "http://" + NC_HOST, nc_login, nc_uid, nc_pw)
            path = fakes.NC_FILES[0][0]
            fw = self.note("anna", "Ferienwohnung Möwe", [
                H("f1", "Unterlagen"), P("f2", "Der Mietvertrag liegt in unserer Nextcloud – hier ist er direkt verlinkt:"),
                {"id": "f3", "type": "nextcloud", "data": {"inst": "http://" + NC_HOST, "fileid": str(nc_ids[path]), "kind": "file",
                                                           "name": path.rsplit("/", 1)[1], "mime": "application/pdf", "size": 48213,
                                                           "mtime": int(time.time() - 3600 * 26)}},
                P("f4", "Restzahlung bis <b>15. Juni</b> überweisen.")], parent_id=self.ids.get("wiki:Urlaub Ostsee 2027"), icon="🏠",
               cover=self.art("anna", "meer", (170, 210, 240), (110, 165, 220), "moewe"))
            self.ids["ferienwohnung"] = fw
        return self.ids


    def calendar(self, on=True):
        """CalDAV-Zugang "Familie" direkt in der DB - nur fuer das Bild b16-01 (ohne CalDAV-Server zeigt "Termine" sonst einen
        Fehler); on=False entfernt ihn wieder."""
        from saga import calendars
        with self.db.connect() as c, c:
            calendars._cal_db(c)
            if not on:
                c.execute("DELETE FROM cal_account WHERE uid='anna'")
                return
            if not c.execute("SELECT 1 FROM cal_account WHERE uid='anna'").fetchone():
                c.execute("INSERT INTO cal_account (uid, label, base_url, username, secret, calendars, created, updated) VALUES (?,?,?,?,?,?,?,?)",
                          ("anna", "Familie", "https://cloud.saga.example/remote.php/dav", "anna",
                           calendars._cal_fernet().encrypt(b"nur-demo").decode(),
                           json.dumps([{"href": "https://cloud.saga.example/remote.php/dav/calendars/anna/familie/", "name": "Familie",
                                        "color": "#e8805c", "show": True, "writable": True}]), time.time(), time.time()))

    # --- Welt Verein ---------------------------------------------------------------------------------------------------
    def verein(self):
        self.users()
        P = lambda i, t: {"id": i, "type": "paragraph", "data": {"text": t}}  # noqa: E731
        self.ids["parzellen"] = self.note("frieda", "Parzellenplan 2027", [P("p1", "Wer bekommt welche Parzelle?")], icon="🌱", cover=None)
        self.service("wiki", "Vereinswiki", "https://wiki.verein.example", 1)
        return self.ids

    def save(self):
        st = os.environ.get("STATE_DIR")
        if st:
            with open(os.path.join(st, "handbuch-demo.json"), "w") as fh:
                json.dump({"welt": self.welt, "base": self.base, "pw": self.pw, "ids": self.ids}, fh)
            os.chmod(os.path.join(st, "handbuch-demo.json"), 0o600)


if __name__ == "__main__":
    w = sys.argv[1] if len(sys.argv) > 1 else "familie"
    sys.path.insert(0, ROOT)
    dm = Demo(w)
    getattr(dm, w)()
    dm.save()
    print("Demo", w, "angelegt:", json.dumps(dm.ids, ensure_ascii=False))
