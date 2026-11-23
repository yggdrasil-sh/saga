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
NC_HOST, BO_HOST, WX_HOST = "cloud.saga.example", "bookorbit.example", "wetter.saga.example"


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
        hd = {"X-Saga": "1", "Cookie": "saga=" + self.ck(u), "Accept-Language": "de-DE,de;q=0.9"}
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
        # Konten der Testumgebung (tools/testumgebung/konten.py) gehoeren nicht in die Demo-Welt (Benutzerliste, Zahlen)
        from saga import userdata
        with self.db.connect() as c:
            extra = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'um-%' OR username LIKE 'hk-test-%'").fetchall()]
        for u in extra:
            userdata.delete_user_data(u, drop_user=True)
        for u, name, adm, groups, *_ in self.W["users"]:
            if not self.auth.get_user(u):
                self.auth.create_user(u, self.pw, name, email=f"{u}@{self.W['host']}", is_admin=adm, groups=groups)
            else:
                self.auth.set_password(u, self.pw)
        from saga import covers
        for u, *_ in self.W["users"]:
            covers.set_random(u, False)            # keine zufaelligen Titelbilder: Demo-Seiten haben gezeichnete oder keine
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

    def service(self, slug, name, url, sort, everyone=True, check=False, cat=None, members=()):
        import auth
        with self.db.connect() as c, c:
            if c.execute("SELECT 1 FROM service WHERE slug=?", (slug,)).fetchone():
                return
            c.execute("INSERT INTO service (slug, name, url, icon, category, extra_groups, everyone, check_status, sort, created) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?)", (slug, name, url, "", cat or self.W["title"].split()[0], "", 1 if everyone else 0,
                                                       1 if check else 0, sort, time.time()))
            auth.ensure_group(c, "s_" + slug, f"Zugang zu {name}")
        for u in members:                                   # Mitglied der Zugangsgruppe s_<slug>
            i = self.auth.get_user(u)
            if i:
                self.auth.set_user_groups(i["id"], sorted(self.auth.groups_of(i["id"])[0] | {"s_" + slug}))

    # --- Welt Familie --------------------------------------------------------------------------------------------------
    def familie(self, nc_login=None, nc_pw=None, nc_uid=None, nc_ids=None):
        today = date.today()
        self.users()
        self.setting("allowed_hosts", ", ".join([NC_HOST, BO_HOST, WX_HOST]))   # nur die Anzeige-Hosts der Attrappen (steht in Bildern)
        # KI-Assistent: Attrappe im Namensraum (http nur mit festgehaltener interner Adresse, wie im Admin-Bereich)
        self.setting("ai", {"enabled": True, "provider": "openai", "url": "http://ki.saga.example/v1", "model": "familie-ki",
                            "api_key": "", "groups": [], "timeout": 60, "pinned": ["127.0.1.6"], "chat": True})
        self.setting("weather", {"enabled": True, "base_url": f"http://{WX_HOST}/v1/forecast", "geo_url": f"http://{WX_HOST}/v1/search", "api_key": ""})
        self.service("eltern", "Elternportal", "https://eltern.example.org", 1)
        self.service("fotos", "Fotos", "https://fotos.saga.example", 2)
        self.service("cloud", "Nextcloud", "https://cloud.saga.example", 3)
        self.service("filme", "Filmabend", "https://filme.saga.example", 4, cat="Freizeit")
        self.service("zuhause", "Zuhause", "https://zuhause.saga.example", 5, cat="Haus", check=True)
        self.service("wetter", "Wetter Ostsee", "https://wetter.example.org", 6, cat="Urlaub")
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
        self.familie_welt(today)
        return self.ids

    # --- Familie: Haushalt, Feste, Urlaub, Schule (Kapitel B02-B15) ------------------------------------------------------
    def share(self, owner, nid, target, role, typ="user"):
        return self.api(owner, "PUT", f"/api/notes/{nid}/shares", {"type": typ, "target": target, "role": role})

    def body(self, u, nid, blocks):
        return self.api(u, "PATCH", f"/api/notes/{nid}", {"format": "blocks", "body": json.dumps({"blocks": blocks}, ensure_ascii=False)})

    def mkdb(self, u, title, icon, props, views, parent=None, cover=None, rows=(), **kw):
        nid = self.note(u, title, kind="database", icon=icon, cover=cover, parent_id=parent, **kw)
        self.api(u, "PATCH", f"/api/notes/{nid}", {"schema": {"props": props, "views": views}})
        out = {}
        for r in rows:
            r = dict(r)
            who = r.pop("_u", u)
            d = self.api(who, "POST", "/api/notes", dict(r, parent_id=nid))
            out[r["title"]] = d.get("id") or (d.get("note") or {}).get("id")
        return nid, out

    def versions(self, owner, nid, steps):
        """Versionsverlauf wie nach einem Tag Arbeit: frühere Stände (die ersten n Blöcke der Seite) mit Autor und Zeit
        (vor x Stunden) - so legt Saga sie beim Speichern an (Tabelle note_version); hier direkt, damit die Zeiten stimmen."""
        with self.db.connect() as c, c:
            title, body = c.execute("SELECT title, body FROM note WHERE id=?", (nid,)).fetchone()
            blocks = json.loads(body or "{}").get("blocks") or []
            for hours, who, n in steps:
                c.execute("INSERT INTO note_version (note_id, uid, title, body, format, created, author) VALUES (?,?,?,?,?,?,?)",
                          (nid, owner, title, json.dumps({"blocks": blocks[:n]}, ensure_ascii=False), "blocks",
                           time.time() - hours * 3600, who))

    def familie_welt(self, today):
        P = lambda i, t: {"id": i, "type": "paragraph", "data": {"text": t}}  # noqa: E731
        H = lambda i, t, lv=2: {"id": i, "type": "header", "data": {"text": t, "level": lv}}  # noqa: E731
        CL = lambda i, e, t: {"id": i, "type": "callout", "data": {"emoji": e, "text": t}}  # noqa: E731
        CHK = lambda i, items: {"id": i, "type": "list", "data": {"style": "checklist", "meta": {}, "items": [  # noqa: E731
            {"content": t, "meta": {"checked": c}, "items": []} for t, c in items]}}
        from saga import core
        REF = core.local_ref                                                          # Handle '~…' eines Kontos dieser Instanz
        M = lambda u, n: f'<span class="hk-mention" data-u="{REF(u)}">{n}</span>'  # noqa: E731
        D = lambda d, lab: f'<time datetime="{d}" class="hk-date">{lab}</time>'  # noqa: E731
        ymd_ = lambda k: ymd(today + timedelta(days=k))  # noqa: E731
        year = today.year + (1 if today.month >= 8 else 0)
        # Haushalt: vier Datenbanken (geteilt mit der Gruppe familie)
        haus = self.note("anna", "Haushalt", [P("h1", "Alles rund ums Haus: Einkauf, Rezepte, Termine und Putzplan.")], icon="🏠")
        self.share("anna", haus, "familie", "edit", "group")
        ein, _ = self.mkdb("anna", "Einkaufsliste", "🛒", [
            {"id": "menge", "name": "Menge", "type": "number"},
            {"id": "preis", "name": "Preis", "type": "number", "fmt": "euro"},
            {"id": "summe", "name": "Summe", "type": "formula", "expr": 'prop("menge") * prop("preis")', "fmt": "euro"},
            {"id": "abt", "name": "Abteilung", "type": "select", "options": [
                {"id": "obst", "name": "Obst & Gemüse", "color": "green"}, {"id": "kuehl", "name": "Kühlregal", "color": "blue"},
                {"id": "vorrat", "name": "Vorrat", "color": "orange"}, {"id": "drog", "name": "Drogerie", "color": "pink"}]},
            {"id": "erledigt", "name": "Erledigt", "type": "checkbox"}],
            [{"id": "liste", "name": "Liste", "type": "table", "calcs": {"summe": "sum"}},
             {"id": "abteilung", "name": "Nach Abteilung", "type": "table", "group": "abt"}],
            parent=haus, rows=[{"title": t, "props": dict(menge=m, preis=pr, abt=a, erledigt=e)} for t, m, pr, a, e in (
                ("Äpfel", 6, 0.45, "obst", False), ("Tomaten", 4, 0.6, "obst", True), ("Milch", 2, 1.19, "kuehl", False),
                ("Butter", 1, 2.29, "kuehl", False), ("Zimt", 1, 1.49, "vorrat", False), ("Mehl", 1, 0.99, "vorrat", True),
                ("Zahnpasta", 2, 1.95, "drog", False))])
        rez, rr = self.mkdb("anna", "Rezepte", "🍲", [
            {"id": "tags", "name": "Tags", "type": "multi_select", "options": [
                {"id": "kuchen", "name": "Kuchen", "color": "orange"}, {"id": "kind", "name": "Kinderliebling", "color": "pink"},
                {"id": "veg", "name": "vegetarisch", "color": "green"}, {"id": "schnell", "name": "schnell", "color": "blue"}]},
            {"id": "vorb", "name": "Vorbereitung (min)", "type": "number"}, {"id": "koch", "name": "Kochen (min)", "type": "number"},
            {"id": "zeit", "name": "Zeit gesamt", "type": "formula", "expr": 'format(prop("vorb") + prop("koch")) + " Min."'},
            {"id": "zutaten", "name": "Zutaten", "type": "multi_select", "options": [
                {"id": i, "name": z, "color": "gray"} for i, z in (("aepfel", "Äpfel"), ("butter", "Butter"), ("zimt", "Zimt"), ("mehl", "Mehl"),
                                                                    ("tomaten", "Tomaten"), ("gurke", "Gurke"), ("feta", "Feta"))]},
            {"id": "von", "name": "Von", "type": "person"}],
            [{"id": "galerie", "name": "Galerie", "type": "gallery", "card": "m", "hidden": ["vorb", "koch", "zutaten"]},
             {"id": "tabelle", "name": "Tabelle", "type": "table"}],
            parent=haus, cover=self.art("anna", "teller", (230, 90, 80), (110, 170, 90), "rezepte-titel"),
            rows=[{"title": t, "cover": self.art("anna", "teller", c1, c2, "rezept-%d" % i),
                   "props": dict(tags=tg, vorb=v, koch=k, zutaten=z, von=[REF(w)])}
                  for i, (t, c1, c2, tg, v, k, z, w) in enumerate((
                      ("Apfelkuchen nach Oma Lena", (200, 40, 40), (200, 110, 40), ["kuchen", "kind"], 30, 45, ["aepfel", "butter", "zimt", "mehl"], "lena"),
                      ("Tomatensuppe", (250, 240, 220), (230, 130, 50), ["veg", "schnell"], 10, 25, ["tomaten"], "anna"),
                      ("Sommersalat", (230, 60, 70), (90, 160, 80), ["veg", "schnell"], 15, 0, ["tomaten", "gurke", "feta"], "ben"),
                      ("Pfannkuchen", (250, 220, 120), (240, 180, 90), ["kind", "schnell"], 10, 15, ["mehl", "butter"], "clara")))])
        self.ids.update(haushalt=haus, einkauf=ein, rezepte=rez, apfelkuchen=rr.get("Apfelkuchen nach Oma Lena"))
        self.share("anna", rez, "lena", "comment")
        kal, _ = self.mkdb("anna", "Familienkalender", "📅", [
            {"id": "wann", "name": "Wann", "type": "date"}, {"id": "wer", "name": "Wer", "type": "person"},
            {"id": "art", "name": "Art", "type": "select", "options": [
                {"id": "schule", "name": "Schule", "color": "blue"}, {"id": "familie", "name": "Familie", "color": "pink"},
                {"id": "arzt", "name": "Arzt", "color": "red"}, {"id": "urlaub", "name": "Urlaub", "color": "green"}]}],
            [{"id": "kalender", "name": "Kalender", "type": "calendar", "date_by": "wann", "range": "week"},
             {"id": "zeitleiste", "name": "Zeitleiste", "type": "timeline", "date_by": "wann"},
             {"id": "tabelle", "name": "Tabelle", "type": "table", "sorts": [{"pid": "wann", "dir": "asc"}]}],
            parent=haus, rows=[{"title": t, "props": dict(wann=w, wer=[REF(u) for u in us], art=a)} for t, w, us, a in (
                ("Elternabend 7b", ymd_(1), ["anna"], "schule"), ("Zahnarzt Clara", ymd_(2), ["clara"], "arzt"),
                ("Oma Lena zu Besuch", f"{ymd_(5)}/{ymd_(7)}", ["lena"], "familie"), ("Sommerfest", ymd_(19), ["anna", "ben"], "familie"),
                ("Klassenfahrt Clara", f"{ymd_(22)}/{ymd_(26)}", ["clara"], "schule"),
                ("Ostsee-Urlaub", f"{year}-07-17/{year}-07-31", ["anna", "ben", "clara"], "urlaub"))])
        hp, _ = self.mkdb("anna", "Hausputz", "🧹", [
            {"id": "status", "name": "Status", "type": "select", "status": True, "options": [
                {"id": "offen", "name": "Offen", "color": "gray", "group": "todo"}, {"id": "dabei", "name": "Dabei", "color": "blue", "group": "doing"},
                {"id": "fertig", "name": "Erledigt", "color": "green", "group": "done"}]},
            {"id": "wer", "name": "Wer", "type": "person"}, {"id": "faellig", "name": "Fällig", "type": "date"},
            {"id": "raum", "name": "Raum", "type": "select", "options": [
                {"id": "kueche", "name": "Küche", "color": "orange"}, {"id": "bad", "name": "Bad", "color": "blue"},
                {"id": "wohn", "name": "Wohnzimmer", "color": "yellow"}, {"id": "garten", "name": "Garten", "color": "green"}]},
            {"id": "aufwand", "name": "Aufwand (min)", "type": "number"}],
            [{"id": "board", "name": "Board", "type": "board", "group_by": "status"},
             {"id": "tabelle", "name": "Tabelle", "type": "table"},
             {"id": "aufwand", "name": "Aufwand je Raum", "type": "chart", "chart": {"type": "bar", "x": "raum", "agg": "sum", "y": "aufwand"}}],
            parent=haus, rows=[{"title": t, "props": dict(status=st, wer=[REF(u)], faellig=ymd_(k), raum=r, aufwand=a)} for t, st, u, k, r, a in (
                ("Bad putzen", "offen", "ben", 1, "bad", 40), ("Kühlschrank auswischen", "offen", "clara", 2, "kueche", 20),
                ("Backofen reinigen", "offen", "anna", 4, "kueche", 50), ("Fenster Wohnzimmer", "dabei", "anna", 0, "wohn", 60),
                ("Rasen mähen", "fertig", "ben", -1, "garten", 45), ("Staubsaugen", "fertig", "clara", -1, "wohn", 25))])
        self.ids.update(kalender=kal, hausputz=hp)
        # Sommerfest (mit Ben bearbeiten) + veroeffentlichte Einladung
        fest = self.note("anna", "Sommerfest", icon="🎉")
        self.body("anna", fest, [
            H("s1", "Sommerfest im Garten"),
            P("s2", f"Samstag ab 14 Uhr. {M('ben', 'Ben Sommer')} kümmert sich ums Grillgut, Oma Lena bringt den Apfelkuchen."),
            CHK("s3", [("Lampions aufhängen", True), ("Getränke kaufen", False), ("Grillkohle", False), ("Zwei Biertischgarnituren leihen", False)]),
            CL("s4", "☀️", "Bei Regen feiern wir in der Garage.")])
        self.share("anna", fest, "familie", "edit", "group")
        inv = self.note("anna", "Sommerfest-Einladung", parent_id=fest, icon="💌",
                        cover=self.art("anna", "blumen", (170, 210, 245), (215, 235, 250), "einladung"))
        self.body("anna", inv, [
            H("e1", "Ihr seid eingeladen!"),
            P("e2", "Liebe Nachbarn und Freunde, wir feiern am <b>Samstag ab 14 Uhr</b> unser Sommerfest im Garten."),
            P("e3", "Bringt gern einen Salat mit – für Grillgut und Getränke ist gesorgt.")])
        self.api("anna", "PUT", f"/api/notes/{inv}/publish", {"slug": "sommerfest", "name": "anna-sommer"})
        self.ids.update(sommerfest=fest, einladung=inv)
        # Urlaub Ostsee 2027 (aus dem Wiki): ausfuehrliche Seite fuer Editor-, Teilen- und Kommentarbilder
        ur = self.ids.get("wiki:Urlaub Ostsee 2027")
        if ur:
            wiki = self.ids["wiki"]
            self.body("anna", ur, [
                H("u1", "Der Plan"),
                P("u2", f"Wir fahren am {D(f'{year}-07-17', '17. Juli ' + str(year))} los. {M('ben', 'Ben Sommer')} bucht die Fähre, "
                        f'die Nummern stehen im <a href="/notes#{wiki}">Familien-Wiki</a>. Fahrzeit grob: <code class="hk-math">t = s / v</code>.'),
                CL("u3", "💡", "Ferienwohnung ist angezahlt – Rest bis <b>15. Juni</b> überweisen."),
                H("u4", "Packliste", 3),
                CHK("u5", [("Badesachen und Handtücher", True), ("Sonnencreme", True), ("Drachen für den Strand", False),
                           ("Ladekabel und Powerbank", False), ("Fahrradschloss", False)]),
                H("u6", "Unterkünfte", 3),
                {"id": "u7", "type": "columns", "data": {"cols": [
                    {"blocks": [H("u7a", "Ferienwohnung Möwe", 4), P("u7b", "3 Zimmer, Balkon mit Meerblick, 5 Minuten zum Strand.")]},
                    {"blocks": [H("u7c", "Pension Seestern", 4), P("u7d", "Frühstück inklusive, Fahrradverleih im Haus.")]}]}},
                H("u8", "Ausflüge", 3),
                {"id": "u9", "type": "table", "data": {"withHeadings": True, "content": [
                    ["Tag", "Ausflug", "Wer"], ["Montag", "Radtour zum Leuchtturm 🚲", "alle"], ["Mittwoch", "Seehundstation", "Clara, Oma"],
                    ["Freitag", "Kutterfahrt", "Ben, Anna"]]}},
                H("u10", "Budget", 3),
                {"id": "u11", "type": "math", "data": {"tex": r"K = 7 \cdot 95\,€ + 2 \cdot 60\,€ = 785\,€"}},
                {"id": "u12", "type": "code", "data": {"language": "mermaid", "view": "preview",
                                                       "code": "flowchart LR\n  A[Packen] --> B[Fähre] --> C[Ferienwohnung] --> D[Strand!]"}},
                P("u13", "")])
            self.versions("anna", ur, [(7.5, "anna", 2), (6.2, "anna", 4), (4.1, "ben", 6), (2.3, "anna", 9), (0.6, "ben", 12)])
            self.share("anna", ur, "ben", "edit")
            self.share("anna", ur, "clara", "comment")
            self.api("anna", "POST", f"/api/notes/{ur}/versions/checkpoint", {})
            r = self.api("ben", "POST", f"/api/notes/{ur}/comments", {"block_id": "u3", "body": "Restzahlung ist überwiesen ✓"})
            if r.get("thread_id"):
                self.api("anna", "POST", f"/api/notes/{ur}/comments", {"thread_id": r["thread_id"], "body": "Super, danke dir!"})
            self.api("clara", "POST", f"/api/notes/{ur}/comments", {"block_id": "u9", "body": "Darf ich bei der Kutterfahrt mit? 🐟"})
            self.ids["urlaub"] = ur
        # Notizen (Editor-Bilder) und Claras Lernplan
        self.ids["elternabend"] = self.note("anna", "Notizen Elternabend", [
            H("n1", "Elternabend 7b"), P("n2", "Themen: Klassenfahrt, neue Mathelehrerin, Handyregeln."),
            P("n3", "Bitte bis Freitag die Einverständniserklärung abgeben."), P("n4", "")], icon="📝")
        lp = self.note("clara", "Lernplan Abi", icon="📚", cover=self.art("clara", "balken", (236, 214, 170), (214, 92, 80), "lernplan"))
        self.body("clara", lp, [H("l1", "Bis zu den Prüfungen"), CHK("l2", [("Mathe: Analysis wiederholen", True),
                  ("Deutsch: Gedichtanalyse üben", False), ("Bio: Genetik-Karteikarten", False)]),
                  {"id": "l3", "type": "toggle", "data": {"title": "Formeln", "open": False, "blocks": [P("l3a", "Ableitung, Integral, Kettenregel")]}}])
        self.api("clara", "PATCH", f"/api/notes/{lp}", {"font": "serif", "wide": True})
        self.ids["lernplan"] = lp
        # Home-Dashboard aus der Vorlagen-Galerie (Wetter: Lübeck, Attrappe wetter.saga.example)
        d = self.api("anna", "POST", "/api/gallery/home-dashboard/use?lang=de",
                     {"examples": True, "weather_place": {"place": "Lübeck", "lat": 53.87, "lon": 10.69}})
        self.ids["home"] = d.get("root")
        # Ben teilt seine Radtour-Planung mit Anna ("Mit mir geteilt")
        rad = self.note("ben", "Radtour an die Schlei", [H("r1", "Route"), P("r2", "Start am Bahnhof, 42 km, Mittag in Kappeln.")], icon="🚲")
        self.share("ben", rad, "anna", "edit")
        self.ids["radtour"] = rad


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
        H = lambda i, t, lv=2: {"id": i, "type": "header", "data": {"text": t, "level": lv}}  # noqa: E731
        self.ids["parzellen"] = self.note("frieda", "Parzellenplan 2027", [
            H("p0", "Parzellen und Pächter"), P("p1", "Wer bekommt welche Parzelle? Bitte bis Ende Januar eintragen."),
            {"id": "p2", "type": "table", "data": {"withHeadings": True, "content": [
                ["Parzelle", "Pächter", "Größe", "Bemerkung"], ["1", "Familie Rose", "320 m²", "Obstbäume"],
                ["2", "Paul Brandt", "280 m²", "Gemüse"], ["3", "frei", "300 m²", "ab April"], ["4", "Anna Sommer", "250 m²", "neu ab 2027"]]}}],
            icon="🌱", cover=None)
        # Foederation (Bilder B11/A07): Instanz darf sich mit saga.example verbinden (im Namensraum per http)
        self.setting("fed_enabled", True)
        self.setting("allowed_hosts", "saga.example")
        self.service("wiki", "Vereinswiki", "https://wiki.verein.example", 1, everyone=False, cat="Verein", members=("frieda",))
        self.service("kalender", "Vereinskalender", "https://kalender.verein.example", 2, everyone=True, check=True, cat="Verein")
        # KI-Assistent (Admin-Bild a05-04): OpenAI-kompatibler Dienst im Vereinsnetz, Adresse festgehalten (Beispieladresse)
        self.setting("ai", {"enabled": True, "provider": "openai", "url": "http://ki.verein.example/v1", "model": "verein-demo",
                            "api_key": "", "groups": [], "timeout": 60, "pinned": ["192.0.2.10"]})
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
