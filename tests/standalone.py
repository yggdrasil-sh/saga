"""Standalone-Funktionen: lokale Anmeldung, Registrierung, Sitzungen, Admin-Bereich (eigener Port),
Dienste ueber die Gruppen-Kennung, eigene Lesezeichen. Wegwerf-Konten st-*, raeumt auf.
Umgebung: HK_TEST_BASE (Hauptseite), HK_TEST_ADMIN (Admin-Port), BASE_URL, DATABASE_URL/POSTGRES_*."""
import http.client, json, os, re, sys, urllib.parse
import auth
from db import connect

B, AD = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099")), urllib.parse.urlparse(os.environ.get("HK_TEST_ADMIN", "http://127.0.0.1:8098"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
PW, PW2 = "Testpasswort-123", "Neues-Passwort-456"
res = []
def ok(n, c): res.append(bool(c)); print(("OK  " if c else "FAIL"), n)


class Client:
    def __init__(self, base, host=None):
        self.base, self.host, self.ck, self.lang = base, host, {}, "de"     # Texte werden auf Deutsch geprueft

    def req(self, m, p, form=None, js=None, api=False):
        c = http.client.HTTPConnection(self.base.hostname, self.base.port, timeout=30)
        h = {"Host": self.host or self.base.netloc}
        if self.lang:
            h["Accept-Language"] = self.lang
        if self.ck:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.ck.items())
        body = None
        if form is not None:
            body = urllib.parse.urlencode(form, doseq=True); h["Content-Type"] = "application/x-www-form-urlencoded"
        if js is not None:
            body = json.dumps(js); h["Content-Type"] = "application/json"
        if api or js is not None:
            h["X-Saga"] = "1"
        c.request(m, p, body=body, headers=h)
        r = c.getresponse(); raw = r.read()
        for sc in r.msg.get_all("set-cookie") or []:
            k, v = sc.split(";")[0].split("=", 1)
            if v in ("", '""') or "max-age=0" in sc.lower() or "expires=thu, 01 jan 1970" in sc.lower():
                self.ck.pop(k, None)
            else:
                self.ck[k] = v
        return r.status, r.getheader("location") or "", raw.decode("utf-8", "replace")

    def csrf(self, path):
        _, _, html = self.req("GET", path)
        m = re.search(r'name="csrf" value="([^"]+)"', html)
        return m.group(1) if m else ""

    def post(self, path, form, src=None):
        f = dict(form); f["csrf"] = self.csrf(src or path)
        return self.req("POST", path, form=f)

    def api(self, m, p, js=None):
        s, _, raw = self.req(m, p, js=js, api=True)
        try:
            return s, json.loads(raw)
        except ValueError:
            return s, raw


def main_client():
    return Client(B, HOST)


def login(user, pw=PW):
    c = main_client()
    s, loc, _ = c.post("/login", {"username": user, "password": pw})
    return c, s, loc


def flash(html):
    m = re.search(r'class="adm-flash[^"]*"[^>]*>([^<]*)<', html)
    return m.group(1) if m else ""


def cleanup():
    with connect() as c, c:
        names = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'st-%'").fetchall()]
        for t in ("task", "note", "note_version", "bookmark", "fav", "device_login", "app_token", "user_kv", "immich_key", "cal_account", "file"):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE 'st-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'st-%'")
        c.execute("DELETE FROM service WHERE slug LIKE 'st-%'")
        c.execute("DELETE FROM groups WHERE name LIKE 's\\_st-%' OR name LIKE 'st-%'")
        c.execute("DELETE FROM login_fail WHERE key LIKE 'u:st-%' OR key LIKE 'ui:st-%'")
        c.execute("DELETE FROM login_fail WHERE key LIKE 'reg:%' OR key LIKE 'ip:%' OR key LIKE 'adm:%'")
    return names


def main():
    cleanup()
    auth.create_user("st-user", PW, "Sigrun Test", "sigrun@example.org")
    auth.create_user("st-other", PW, "Other Test")
    auth.create_user("st-admin", PW, "Admin Test", is_admin=True)

    # --- Anmeldung Hauptseite ---------------------------------------------------------------
    c = main_client()
    s, loc, html = c.req("GET", "/")
    ok("Ohne Sitzung -> /login", s == 303 and loc == "/login")
    s, _, html = c.req("GET", "/login")
    ok("Anmeldeseite mit CSRF-Feld", s == 200 and 'name="csrf"' in html and "Anmelden" in html)
    s, _, _ = c.req("POST", "/login", form={"username": "st-user", "password": PW})
    ok("Login ohne CSRF-Token -> 400", s == 400 and "uid" not in str(c.ck))
    ok("Falsches Passwort -> 401", login("st-user", "falsch-falsch")[1] == 401)
    u, s, loc = login("st-user")
    ok(f"Richtiges Passwort -> {s} {loc}", s == 303 and loc == "/")
    s, _, html = u.req("GET", "/")
    ok("Dashboard nach Login", s == 200 and "Sigrun Test" in html and "+ Lesezeichen" in html)
    s, a = u.api("GET", "/api/account")
    ok("Eigenes Konto (/api/account)", s == 200 and a["username"] == "st-user" and a["can_edit"] and not a["is_admin"])
    s, _, _ = u.req("GET", "/notes")
    ok("Notizen-Seite", s == 200)

    # --- Admin-Bereich --------------------------------------------------------------------------
    adm = Client(AD)
    s, loc, _ = adm.req("GET", "/")
    ok("Admin ohne Sitzung -> /login", s == 303 and loc == "/login")
    s, _, html = adm.post("/login", {"username": "st-user", "password": PW})
    ok("Admin-Login ohne Admin-Rechte abgewiesen", s == 401 and "keine Admin-Rechte" in html)
    s, loc, _ = adm.post("/login", {"username": "st-admin", "password": PW})
    ok("Admin-Login", s == 303 and loc == "/")
    ok("Haupt-Sitzungscookie gilt nicht im Admin-Bereich", Client(AD).req("GET", "/users")[0] == 303)
    pages = ["/", "/users", "/groups", "/services", "/settings", "/settings/oidc", "/settings/ldap"]
    codes = {p: adm.req("GET", p)[0] for p in pages}
    ok(f"Alle Admin-Seiten laden {codes}", all(v == 200 for v in codes.values()))
    s, _, _ = adm.req("POST", "/settings", form={"registration": "1", "group_prefix": "s_", "local_login": "1"})
    ok("Einstellungen ohne CSRF wirkungslos", s == 303 and not auth.settings(fresh=True)["registration"])

    # Registrierung
    s, loc, _ = main_client().req("GET", "/register")
    ok("Registrierung aus -> /login", s == 303 and loc == "/login")
    s, loc, _ = adm.post("/settings", {"title": "Testwarte", "registration": "1", "local_login": "1", "group_prefix": "s_",
                                       "default_groups": "st-alle", "allowed_hosts": ""})
    ok("Allgemein speichern (Registrierung an, Standardgruppe)", s == 303 and auth.settings(fresh=True)["registration"])
    r = main_client()
    s, loc, html = r.post("/register", {"username": "st-reg", "name": "Neu", "email": "kaputt", "password": PW, "password2": PW}, "/register")
    ok("Registrierung: kaputte E-Mail abgewiesen", s == 400 and "E-Mail" in html)
    s, loc, html = r.post("/register", {"username": "st-reg", "name": "Neu", "email": "", "password": PW, "password2": PW + "x"}, "/register")
    ok("Registrierung: Passwoerter verschieden", s == 400)
    s, loc, html = r.post("/register", {"username": "St-Reg", "name": "Neu Reg", "email": "neu@example.org", "password": PW, "password2": PW}, "/register")
    ok(f"Registrierung -> angemeldet ({s} {loc})", s == 303 and loc == "/" and r.api("GET", "/api/account")[1].get("username") == "st-reg")
    ok("Neues Konto hat Standardgruppe", "st-alle" in auth.user_info("st-reg", fresh=True)["groups"])
    s, _, html = main_client().post("/register", {"username": "st-user", "password": PW, "password2": PW}, "/register")
    ok("Registrierung: vergebener Name abgewiesen", s == 400 and "gibt es schon" in html)
    s, _, html = main_client().req("GET", "/login")
    ok("Titel aus den Einstellungen + Registrieren-Link", "Testwarte" in html and "/register" in html)

    # --- Dienste ueber die Gruppen-Kennung ----------------------------------------------------------
    s, loc, html = adm.post("/services", {"action": "save", "name": "St Wiki", "url": "https://wiki.example.org", "slug": "st-wiki",
                                          "icon": "bookstack", "category": "Wissen", "check_status": ""}, "/services")
    ok("Dienst anlegen", s == 303)
    with connect() as cn:
        sid = cn.execute("SELECT id FROM service WHERE slug='st-wiki'").fetchone()[0]
        ok("Zugangsgruppe s_st-wiki automatisch angelegt", cn.execute("SELECT 1 FROM groups WHERE name='s_st-wiki'").fetchone())
    names = lambda cl: [x["name"] for x in cl.api("GET", "/api/services")[1]]
    ok("Ohne Gruppe nicht sichtbar", "St Wiki" not in names(u))
    uid_user = auth.get_user("st-user")["id"]
    s, _, _ = adm.post(f"/users/{uid_user}", {"action": "save", "name": "Sigrun Test", "email": "sigrun@example.org", "groups": ["s_st-wiki"]})
    ok("Admin: Benutzer in Gruppe s_st-wiki", s == 303 and "s_st-wiki" in auth.user_info("st-user")["groups"])
    ok("Mit Gruppe sichtbar (Kachel + /api/services)", "St Wiki" in names(u) and "St Wiki" in u.req("GET", "/")[2])
    o, _, _ = login("st-other")
    ok("Andere Person sieht ihn nicht", "St Wiki" not in names(o))
    adm.post("/services", {"action": "save", "id": sid, "name": "St Wiki", "url": "https://wiki.example.org", "slug": "st-wiki",
                           "category": "Wissen", "extra": "st-alle"}, f"/services/{sid}")
    ok("Zusatzgruppe gibt frei (Registrierte haben st-alle)", "St Wiki" in names(r) and "St Wiki" not in names(o))
    adm.post("/services", {"action": "save", "id": sid, "name": "St Wiki", "url": "https://wiki.example.org", "slug": "st-wiki",
                           "category": "Wissen", "everyone": "1"}, f"/services/{sid}")
    ok("Fuer alle", "St Wiki" in names(o))
    s, _, html = adm.post("/services", {"action": "save", "name": "Boese", "url": "javascript:alert(1)", "slug": "st-boese"}, "/services")
    ok("Dienst mit javascript:-Adresse abgewiesen", "http://" in flash(adm.req("GET", "/services")[2]) or not cn_has("SELECT 1 FROM service WHERE slug='st-boese'"))

    # --- Lesezeichen --------------------------------------------------------------------------------
    ok("Lesezeichen javascript:/data:/Zugangsdaten abgewiesen", all(u.api("POST", "/api/bookmarks", {"name": "x", "url": x})[0] == 400
       for x in ("javascript:alert(1)", "data:text/html,x", "https://user:pw@example.org", "ftp://example.org")))
    s, bm = u.api("POST", "/api/bookmarks", {"name": "Router", "url": "192.168.1.1", "icon": "openwrt", "category": "Netz"})
    ok(f"Lesezeichen anlegen (ohne https:// ergaenzt: {bm.get('url') if isinstance(bm, dict) else bm})", s == 200 and bm["url"] == "https://192.168.1.1")
    sv = {x["name"]: x for x in u.api("GET", "/api/services")[1]}
    ok("Lesezeichen in /api/services (own) fuer die Apps", sv.get("Router", {}).get("own") is True and sv.get("St Wiki", {}).get("own") is False)
    ok("Andere Person: Lesezeichen unsichtbar", "Router" not in names(o))
    ok("Andere Person kann es nicht aendern/loeschen", o.api("PATCH", f"/api/bookmarks/{bm['id']}", {"name": "x"})[0] == 404
       and o.api("DELETE", f"/api/bookmarks/{bm['id']}")[0] == 404)
    ok("Andere Person kann es nicht favorisieren", o.api("POST", "/api/fav", {"group": bm["key"]})[0] == 403)
    ok("Favorit auf eigenes Lesezeichen", u.api("POST", "/api/fav", {"group": bm["key"]})[1].get("fav") is True)
    s, b2 = u.api("PATCH", f"/api/bookmarks/{bm['id']}", {"name": "Router OpenWrt"})
    ok("Lesezeichen umbenennen", s == 200 and b2["name"] == "Router OpenWrt" and b2["url"] == "https://192.168.1.1")
    html = u.req("GET", "/")[2]
    ok("Kachel auf dem Dashboard (Favorit, ohne Status-Punkt, mit ✎)", "Router OpenWrt" in html and 'data-bm="' in html)
    st = u.api("GET", "/api/status")[1]
    ok("Status nie fuer Lesezeichen", bm["key"] not in st)
    ok("Lesezeichen loeschen (Favorit mit)", u.api("DELETE", f"/api/bookmarks/{bm['id']}")[0] == 200
       and not cn_has("SELECT 1 FROM fav WHERE uid='st-user' AND grp=?", (bm["key"],)))

    # --- Sitzungen ----------------------------------------------------------------------------------
    u2, _, _ = login("st-user")
    ok("Zweite Sitzung", u2.api("GET", "/api/account")[0] == 200)
    s, d = u.api("POST", "/api/account/password", {"old": "falsch", "new": PW2})
    ok("Passwort aendern: altes falsch -> 403", s == 403)
    s, d = u.api("POST", "/api/account/password", {"old": PW, "new": "kurz"})
    ok("Passwort aendern: zu kurz -> 400", s == 400)
    s, d = u.api("POST", "/api/account/password", {"old": PW, "new": PW2})
    ok("Passwort aendern", s == 200)
    ok("Diese Sitzung bleibt", u.api("GET", "/api/account")[0] == 200)
    ok("Andere Sitzung endet", u2.api("GET", "/api/account")[0] == 401)
    ok("Neues Passwort gilt", login("st-user", PW2)[1] == 303 and login("st-user", PW)[1] == 401)
    s, d = u.api("PATCH", "/api/account", {"name": "Sigrun Neu", "email": "neu@example.org"})
    ok("Name/E-Mail aendern", s == 200 and auth.get_user("st-user")["display_name"] == "Sigrun Neu")
    adm.post(f"/users/{uid_user}", {"action": "save", "name": "Sigrun Neu", "disabled": "1", "groups": ["s_st-wiki"]})
    ok("Admin sperrt -> Sitzung sofort ungueltig", u.api("GET", "/api/account")[0] == 401 and login("st-user", PW2)[1] == 401)
    adm.post(f"/users/{uid_user}", {"action": "save", "name": "Sigrun Neu", "groups": ["s_st-wiki"]})
    u, s, _ = login("st-user", PW2)
    ok("Entsperrt -> Anmeldung wieder moeglich", s == 303)
    adm.post(f"/users/{uid_user}", {"action": "logout"})
    ok("Admin: ueberall abmelden", u.api("GET", "/api/account")[0] == 401)
    s, _, _ = adm.post(f"/users/{uid_user}", {"action": "password", "password": PW})
    ok("Admin setzt Passwort", s == 303 and login("st-user", PW)[1] == 303)

    # lokale Anmeldung abschalten: Hauptseite nein, Admin-Bereich ja
    adm.post("/settings", {"title": "Testwarte", "registration": "", "group_prefix": "s_", "default_groups": "", "allowed_hosts": ""})
    lc, s, _ = login("st-user")
    ok(f"Lokale Anmeldung aus -> abgewiesen ({s})", s != 303 and lc.api("GET", "/api/account")[0] == 401
       and "keine Anmeldeart" in main_client().req("GET", "/login")[2])
    ok("Registrierung wieder aus", main_client().req("GET", "/register")[0] == 303)
    ok("Admin kommt weiterhin in den Admin-Bereich", Client(AD).post("/login", {"username": "st-admin", "password": PW})[0] == 303)
    adm.post("/settings", {"title": "Saga", "local_login": "1", "group_prefix": "s_", "default_groups": "", "allowed_hosts": ""})

    # Schutz vor Selbstaussperrung, CSRF, Fehlversuche
    me_id = auth.get_user("st-admin")["id"]
    adm.post(f"/users/{me_id}", {"action": "save", "name": "Admin Test"})
    ok("Eigene Admin-Rechte nicht entziehbar", auth.get_user("st-admin")["is_admin"] == 1)
    s, _, _ = adm.req("POST", f"/users/{uid_user}", form={"action": "delete", "confirm": "st-user"})
    ok("Loeschen ohne CSRF wirkungslos", auth.get_user("st-user") is not None)
    for _ in range(9):
        login("st-other", "falsch-falsch")
    _, s, _ = login("st-other")
    ok("Nach 8 Fehlversuchen gesperrt (auch mit richtigem Passwort)", s == 401)
    auth.clear_fails("st-other")

    # Konto loeschen -> alle Daten weg
    rid = auth.get_user("st-reg")["id"]
    r.api("POST", "/api/notes", {"title": "Geheim"}); r.api("POST", "/api/bookmarks", {"url": "https://example.org"})
    s, _, _ = adm.post(f"/users/{rid}", {"action": "delete", "confirm": "falsch"})
    ok("Loeschen mit falscher Bestaetigung wirkungslos", auth.get_user("st-reg") is not None)
    adm.post(f"/users/{rid}", {"action": "delete", "confirm": "st-reg"})
    ok("Konto + alle Daten geloescht", auth.get_user("st-reg") is None and not cn_has("SELECT 1 FROM note WHERE uid='st-reg'")
       and not cn_has("SELECT 1 FROM bookmark WHERE uid='st-reg'") and r.api("GET", "/api/notes")[0] == 401)

    # Admin: Benutzer anlegen + Gruppen, LDAP/OIDC-Tests ohne Server
    s, loc, _ = adm.post("/users/new", {"username": "st-neu", "name": "Neu", "source": "local", "password": PW, "groups": "s_st-wiki, st-team"}, "/users")
    ok("Admin legt Benutzer an (mit Gruppen)", s == 303 and {"s_st-wiki", "st-team"} <= auth.user_info("st-neu")["groups"])
    adm.post("/settings/ldap", {"action": "test", "url": "ldaps://127.0.0.1:1", "user_base": "dc=x", "user_filter": "(uid={username})"})
    html = adm.req("GET", "/settings/ldap")[2]
    ok("LDAP-Test ohne Server -> verstaendliche Meldung", "✗" in html)
    adm.post("/settings/oidc", {"action": "test", "issuer": "https://127.0.0.1:1/realms/x", "client_id": "x"})
    html = adm.req("GET", "/settings/oidc")[2]
    ok("OIDC-Test ohne Server -> verstaendliche Meldung", "✗" in html and "/auth/callback" in html)
    s, _, html = main_client().req("GET", "/login")
    ok("SSO-Knopf nur wenn eingerichtet", "/login/sso" not in html)

    # Abmelden
    s, loc, _ = o.req("GET", "/logout")
    ok("Abmelden", s == 303 and loc.startswith("/login") and o.api("GET", "/api/account")[0] == 401)


def snapshot_settings():
    with connect() as c:
        return c.execute("SELECT k, v FROM setting").fetchall()


def restore_settings(snap):
    """Einstellungen wie vor dem Test (der Test-Stack teilt sich die Datenbank mit der laufenden Instanz)."""
    with connect() as c, c:
        c.execute("DELETE FROM setting")
        for k, v in snap:
            c.execute("INSERT INTO setting (k, v) VALUES (?, ?)", (k, v))
    auth._settings_cache["v"] = None


def cn_has(sql, args=()):
    with connect() as c:
        return c.execute(sql, args).fetchone() is not None


if __name__ == "__main__":
    snap = snapshot_settings()
    try:
        main()
    except Exception as ex:  # noqa: BLE001
        import traceback; traceback.print_exc(); ok(f"Abbruch: {ex}", False)
    finally:
        cleanup()
        restore_settings(snap)
    print(f"\nSTANDALONE {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if res and all(res) else 1)
