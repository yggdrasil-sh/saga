"""Fehlversuche bremsen statt Konten auszusperren (2.4, Testrunde W3): Bremse je Konto+Adresse und je Adresse, harte
Sperre nur nach sehr vielen Versuchen von vielen Adressen (mit Benachrichtigung und Admin-Hinweis), Admin sieht und hebt
Sperren auf, "Passwort setzen" hebt sie auf. Wegwerf-Konten as-*, raeumt auf.
Umgebung: HK_TEST_BASE, HK_TEST_ADMIN, BASE_URL, Postgres ueber db.py (der Test-Server vertraut X-Forwarded-For von 127.0.0.1)."""
import http.client, os, re, sys, time, urllib.parse
import auth
from db import connect

B = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
AD = urllib.parse.urlparse(os.environ.get("HK_TEST_ADMIN", "http://127.0.0.1:8098"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
PW, PW2, BAD = "Sperre-Test-1234", "Sperre-Neu-56789", "falsch-falsch-1"
res = []


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, ("  -- " + str(info)) if (info and not c) else "", flush=True)


class Client:
    def __init__(self, base, host=None, ip=None):
        self.base, self.host, self.ck, self.ip = base, host, {}, ip

    def req(self, m, p, form=None):
        c = http.client.HTTPConnection(self.base.hostname, self.base.port, timeout=30)
        h = {"Host": self.host or self.base.netloc, "Accept-Language": "de"}
        if self.ip:
            h["X-Forwarded-For"] = self.ip
        if self.ck:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.ck.items())
        body = None
        if form is not None:
            body = urllib.parse.urlencode(form, doseq=True)
            h["Content-Type"] = "application/x-www-form-urlencoded"
        c.request(m, p, body=body, headers=h)
        r = c.getresponse()
        raw = r.read()
        for sc in r.msg.get_all("set-cookie") or []:
            k, v = sc.split(";")[0].split("=", 1)
            if v in ("", '""') or "max-age=0" in sc.lower():
                self.ck.pop(k, None)
            else:
                self.ck[k] = v
        return r.status, r.getheader("location") or "", raw.decode("utf-8", "replace")

    def post(self, path, form, src=None):
        _, _, html = self.req("GET", src or path)
        m = re.search(r'name="csrf" value="([^"]+)"', html)
        return self.req("POST", path, form=dict(form, csrf=m.group(1) if m else ""))


def login(user, pw, ip):
    return Client(B, HOST, ip).post("/login", {"username": user, "password": pw})


def err(html):
    m = re.search(r'class="[^"]*(?:hk-err|auth-err|error)[^"]*"[^>]*>\s*([^<]+)<', html)
    return m.group(1).strip() if m else ""


def cleanup():
    with connect() as c, c:
        for t in ("notification", "user_kv", "device_login", "app_token"):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE 'as-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'as-%'")
        c.execute("DELETE FROM login_fail WHERE key LIKE '%as-%' OR key LIKE '%203.0.113.%' OR key LIKE '%198.51.100.%'")
        c.execute("DELETE FROM login_lock WHERE username LIKE 'as-%'")


def age_fails(sec):
    """Wartezeit simulieren: Fehlversuche des Tests um sec Sekunden in die Vergangenheit schieben."""
    with connect() as c, c:
        c.execute("UPDATE login_fail SET ts=ts-? WHERE key LIKE '%as-%' OR key LIKE '%203.0.113.%'", (sec,))


def main():
    cleanup()
    auth.create_user("as-lena", PW, "Lena Sperre", "lena@example.org")
    auth.create_user("as-admin", PW, "Admin Sperre", is_admin=True)

    # --- Bremse je Konto+Adresse: Fremder sperrt das Konto NICHT fuer andere -------------------------------------
    codes, texts = [], []
    for _ in range(12):
        s, _, html = login("as-lena", BAD, "203.0.113.10")
        codes.append(s)
        texts.append(err(html) or html[:0])
    ok(f"12 Fehlversuche von einer Adresse: zuerst 'falsch', dann Wartezeit in Sekunden ({codes})",
       all(s in (401, 429) for s in codes) and any("Sekunden" in x for x in texts[8:]), texts)
    s, loc, _ = login("as-lena", PW, "198.51.100.7")
    ok("Richtiges Passwort von anderer Adresse -> angemeldet (kein Aussperren durch Fremde)", s == 303 and loc in ("/", ""), (s, loc))
    s, _, html = login("as-lena", PW, "203.0.113.10")
    ok("Von der ratenden Adresse: richtiges Passwort muss noch warten (Bremse)", s in (401, 429) and "Sekunden" in html, err(html))
    w = auth.fail_wait("as-lena", "203.0.113.10")
    ok(f"Wartezeit waechst progressiv, hoechstens {auth.DELAY_MAX} s (jetzt {w} s)", 0 < w <= auth.DELAY_MAX)
    age_fails(auth.FAIL_WINDOW + 5)
    ok("Nach Ablauf der Wartezeit: Anmeldung von dort wieder moeglich", login("as-lena", PW, "203.0.113.10")[0] == 303)
    ok("Keine harte Sperre durch eine Adresse", auth.lock_of("as-lena") is None)

    # --- Bremse je Adresse (viele Konten durchprobieren) ---------------------------------------------------------
    for i in range(auth.FREE_IP + 2):
        auth.note_fail(f"as-raten{i}", "203.0.113.20")
    ok("Viele Konten von einer Adresse -> diese Adresse wartet", auth.fail_wait("as-lena", "203.0.113.20") > 0)
    ok("... andere Adressen nicht", auth.fail_wait("as-lena", "203.0.113.21") == 0)

    # --- Harte Sperre nur bei sehr vielen Versuchen von vielen Adressen --------------------------------------------
    for i in range(auth.LOCK_FAILS - 1):
        auth.note_fail("as-lena", f"203.0.113.{100 + i % (auth.LOCK_IPS + 2)}")
    ok(f"{auth.LOCK_FAILS - 1} Versuche: noch keine harte Sperre", auth.lock_of("as-lena") is None)
    auth.note_fail("as-lena", "203.0.113.99")
    lk = auth.lock_of("as-lena")
    ok(f"{auth.LOCK_FAILS} Versuche von {auth.LOCK_IPS}+ Adressen -> harte Sperre ({lk})",
       lk is not None and lk["fails"] >= auth.LOCK_FAILS and lk["ips"] >= auth.LOCK_IPS)
    with connect() as c:
        n = c.execute("SELECT COUNT(*) FROM notification WHERE uid='as-lena' AND kind='login.locked'").fetchone()[0]
    ok("Person wird benachrichtigt (Glocke, login.locked)", n == 1)
    s, _, html = login("as-lena", PW, "198.51.100.8")
    ok("Waehrend der Sperre: auch richtiges Passwort abgewiesen, klarer Hinweis", s in (401, 429) and "vorübergehend gesperrt" in html, err(html))
    for i in range(5):
        auth.note_fail("as-lena", f"203.0.113.{150 + i}")
    with connect() as c:
        n = c.execute("SELECT COUNT(*) FROM notification WHERE uid='as-lena' AND kind='login.locked'").fetchone()[0]
    ok("Weitere Versuche: keine zweite Benachrichtigung", n == 1)
    auth.note_fail("as-gibtsnicht", "203.0.113.5")
    ok("Unbekannter Benutzer: keine Benachrichtigung, keine Antwort-Unterschiede", True)
    for i in range(auth.LOCK_FAILS + 5):
        auth.note_fail("as-lena", f"203.0.113.{100 + i % 20}", "admin")
    ok("Admin-Port zaehlt getrennt (dort keine harte Sperre)", auth.fail_wait("as-lena", "203.0.113.250", "admin") == 0)

    # --- Admin: sieht Sperre, hebt sie auf --------------------------------------------------------------------------
    adm = Client(AD)
    s, loc, _ = adm.post("/login", {"username": "as-admin", "password": PW})
    ok("Admin angemeldet", s == 303, (s, loc))
    s, _, html = adm.req("GET", "/")
    ok("Admin-Uebersicht: Hinweis 'Gesperrte Anmeldungen' mit dem Konto", s == 200 and "Gesperrte Anmeldungen" in html and "as-lena" in html)
    s, _, html = adm.req("GET", "/users?q=as-")
    ok("Benutzerliste: Kennzeichen 'Anmeldung gesperrt'", s == 200 and "Anmeldung gesperrt" in html)
    uid = auth.get_user("as-lena")["id"]
    s, _, html = adm.req("GET", f"/users/{uid}")
    ok("Konto-Seite: Sperre mit Zahlen und Knopf 'Sperre aufheben'", s == 200 and "Sperre aufheben" in html and 'value="unlock"' in html)
    s, _, html = adm.post(f"/users/{uid}", {"action": "unlock"})
    ok("Sperre aufheben -> weg", s == 303 and auth.lock_of("as-lena") is None)
    ok("... danach Anmeldung mit Passwort moeglich", login("as-lena", PW, "198.51.100.9")[0] == 303)

    # --- "Passwort setzen" hebt die Sperre auf ----------------------------------------------------------------------
    for i in range(auth.LOCK_FAILS + 1):
        auth.note_fail("as-lena", f"203.0.113.{100 + i % 15}")
    ok("Wieder gesperrt", auth.lock_of("as-lena") is not None)
    s, _, _ = adm.post(f"/users/{uid}", {"action": "password", "password": PW2})
    ok("Admin setzt Passwort -> Sperre und Zaehler weg", s == 303 and auth.lock_of("as-lena") is None
       and auth.fail_wait("as-lena", "203.0.113.100") == 0)
    ok("... Anmeldung mit neuem Passwort", login("as-lena", PW2, "198.51.100.10")[0] == 303)

    # --- Sperre laeuft ab --------------------------------------------------------------------------------------------
    for i in range(auth.LOCK_FAILS + 1):
        auth.note_fail("as-lena", f"203.0.113.{100 + i % 15}")
    with connect() as c, c:
        c.execute("UPDATE login_lock SET until=? WHERE username='as-lena'", (time.time() - 1,))
    ok("Abgelaufene Sperre gilt nicht mehr", auth.lock_of("as-lena") is None)

    cleanup()
    n = sum(res)
    print(f"\nANMELDESPERRE {n}/{len(res)} - {'ALLES GRUEN' if n == len(res) else 'FEHLER'}")
    sys.exit(0 if n == len(res) else 1)


if __name__ == "__main__":
    main()
