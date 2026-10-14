"""Ende-zu-Ende: LDAP (OpenLDAP-Testserver ldap.test) und SSO/OIDC (Testanbieter mockoidc), eingerichtet
ueber den Admin-Bereich wie im echten Betrieb. Wegwerf-Konten st-*, raeumt auf.
Umgebung wie standalone.py, dazu HK_LDAP_ADMIN_PW (Admin-Passwort des Test-LDAP)."""
import json, os, re, sys, urllib.parse
from ldap3 import Connection, Server, MODIFY_DELETE, MODIFY_ADD
import auth
from standalone import AD, PW, Client, cleanup, cn_has, login, main_client, ok, res, restore_settings, snapshot_settings

BASE_DN = "dc=example,dc=org"
LPW = "Ldap-Passwort-789"


def seed():
    c = Connection(Server("ldap://ldap.test", connect_timeout=5), f"cn=admin,{BASE_DN}", os.environ["HK_LDAP_ADMIN_PW"], auto_bind=True)
    for dn in [f"cn={g},ou=groups,{BASE_DN}" for g in ("s_st-wiki", "st-admins", "st-hk-users")] + \
              [f"uid={u},ou=people,{BASE_DN}" for u in ("st-ldap", "st-ldap2", "st-clash", "st-outsider", "st-ssoldap")] + \
              [f"ou=groups,{BASE_DN}", f"ou=people,{BASE_DN}"]:
        c.delete(dn)
    c.add(f"ou=people,{BASE_DN}", "organizationalUnit"); c.add(f"ou=groups,{BASE_DN}", "organizationalUnit")
    for u, name in (("st-ldap", "Ldap Lotte"), ("st-ldap2", "Ldap Zwei"), ("st-clash", "Clash"), ("st-outsider", "Draussen"), ("st-ssoldap", "Sso Ldap")):
        assert c.add(f"uid={u},ou=people,{BASE_DN}", "inetOrgPerson",
                     {"cn": name, "sn": name.split()[-1], "mail": f"{u}@example.org", "userPassword": LPW}), c.result
    user = lambda u: f"uid={u},ou=people,{BASE_DN}"  # noqa: E731
    for g, members in (("s_st-wiki", ["st-ldap", "st-ssoldap"]), ("st-admins", ["st-ldap", "st-clash"]), ("st-hk-users", ["st-ldap", "st-ldap2", "st-clash"])):
        assert c.add(f"cn={g},ou=groups,{BASE_DN}", "groupOfNames", {"member": [user(m) for m in members]}), c.result
    return c


def sso_login(user, claims, start=None):
    """Kompletter OIDC-Ablauf durch den Testanbieter (interaktives Formular) -> (client, status, location)."""
    c = start or main_client()
    s, loc, _ = c.req("GET", "/login/sso")
    if s != 302 and s != 307 and s != 303:
        return c, s, loc
    idp = urllib.parse.urlparse(loc)
    idp_c = Client(idp)
    s, _, html = idp_c.req("GET", idp.path + "?" + idp.query)
    s, back, _ = idp_c.req("POST", idp.path + "?" + idp.query, form={"username": user, "claims": json.dumps(claims)})
    cb = urllib.parse.urlparse(back)
    if not cb.path.endswith("/auth/callback"):
        return c, s, back
    s, loc, html = c.req("GET", cb.path + "?" + cb.query)
    return c, s, loc if s == 303 else html


def main():
    cleanup()
    auth.create_user("st-admin", PW, "Admin", is_admin=True)
    auth.create_user("st-clash", PW, "Lokal Clash")              # lokales Konto mit gleichem Namen wie im LDAP
    auth.create_user("st-local", PW, "Lokal")                     # lokales Konto, Name kommt auch per SSO
    lc = seed()
    adm = Client(AD)
    ok("Admin-Login", adm.post("/login", {"username": "st-admin", "password": PW})[0] == 303)
    adm.post("/services", {"action": "save", "name": "St Wiki", "url": "https://wiki.example.org", "slug": "st-wiki", "category": "Wissen"}, "/services")

    # --- LDAP ---------------------------------------------------------------------------------------
    adm.post("/settings/ldap", {"action": "preset", "preset": "openldap"})
    ldap = {"enabled": "1", "preset": "openldap", "url": "ldap://ldap.test", "starttls": "1", "verify_tls": "",
            "bind_dn": f"cn=admin,{BASE_DN}", "bind_password": os.environ["HK_LDAP_ADMIN_PW"], "user_base": f"ou=people,{BASE_DN}",
            "user_filter": "(&(objectClass=inetOrgPerson)(uid={username}))", "attr_username": "uid", "attr_name": "cn",
            "attr_mail": "mail", "group_mode": "search", "group_base": f"ou=groups,{BASE_DN}", "group_filter": "(member={dn})",
            "group_name_attr": "cn", "auto_create": "1", "admin_group": "st-admins", "password_login": "1"}
    adm.post("/settings/ldap", dict(ldap, action="test", test_user="st-ldap"))
    html = adm.req("GET", "/settings/ldap")[2]
    ok("LDAP-Test: Verbindung (StartTLS) + Benutzer + Gruppen", "✓ Verbindung" in html and "s_st-wiki" in html and "st-admins" in html)
    adm.post("/settings/ldap", dict(ldap, action="save"))
    st = auth.settings(fresh=True)["ldap"]
    ok("LDAP gespeichert, Passwort verschluesselt abgelegt",
       st["enabled"] and st["bind_password"] == os.environ["HK_LDAP_ADMIN_PW"]
       and not cn_has("SELECT 1 FROM setting WHERE k='ldap' AND strpos(v, ?) > 0", (os.environ["HK_LDAP_ADMIN_PW"],)))
    adm.post("/settings/ldap", dict(ldap, action="save", bind_password=""))
    ok("Leeres Passwortfeld behaelt das gespeicherte", auth.settings(fresh=True)["ldap"]["bind_password"] == os.environ["HK_LDAP_ADMIN_PW"])
    ok("LDAP: falsches Passwort -> 401", login("st-ldap", "falsch-falsch")[1] == 401)
    u, s, loc = login("st-ldap", LPW)
    ok(f"LDAP-Anmeldung legt Konto an ({s})", s == 303 and auth.get_user("st-ldap")["source"] == "ldap")
    i = auth.user_info("st-ldap", fresh=True)
    ok(f"LDAP-Gruppen uebernommen {sorted(i['ext_groups'])}", {"s_st-wiki", "st-admins", "st-hk-users"} <= i["ext_groups"])
    ok("Name/E-Mail aus dem Verzeichnis", i["name"] == "Ldap Lotte" and i["mail"] == "st-ldap@example.org")
    ok("Dienst ueber LDAP-Gruppe s_st-wiki sichtbar", any(x["name"] == "St Wiki" for x in u.api("GET", "/api/services")[1]))
    ok("Admin-Gruppe -> Admin-Rechte, Login im Admin-Bereich mit LDAP-Passwort",
       i["is_admin"] and Client(AD).post("/login", {"username": "st-ldap", "password": LPW})[0] == 303)
    ok("LDAP-Konto kann hier kein Passwort aendern", u.api("POST", "/api/account/password", {"old": LPW, "new": "x" * 12})[0] == 403)
    ok("Lokales Konto mit gleichem Namen wird nicht uebernommen", login("st-clash", LPW)[1] == 401 and auth.get_user("st-clash")["source"] == "local")
    lc.modify(f"cn=s_st-wiki,ou=groups,{BASE_DN}", {"member": [(MODIFY_ADD, [f"uid=st-ldap2,ou=people,{BASE_DN}"])]})
    lc.modify(f"cn=st-admins,ou=groups,{BASE_DN}", {"member": [(MODIFY_DELETE, [f"uid=st-ldap,ou=people,{BASE_DN}"])]})
    i = auth.user_info("st-ldap", fresh=True)
    ok("Gruppen werden bei jeder Anmeldung/Erneuerung neu gelesen (Admin entzogen)", "st-admins" not in i["groups"] and not i["is_admin"])
    adm.post("/settings", {"title": "Saga", "registration": "1", "local_login": "1", "group_prefix": "s_", "default_groups": "", "allowed_hosts": ""})
    s, _, html = main_client().post("/register", {"username": "st-ldap2", "password": PW, "password2": PW}, "/register")
    ok("Registrierung mit LDAP-Namen blockiert (kein Wegschnappen)", s == 400 and auth.get_user("st-ldap2") is None)
    adm.post("/settings", {"title": "Saga", "local_login": "1", "group_prefix": "s_", "default_groups": "", "allowed_hosts": ""})
    adm.post("/settings/ldap", dict(ldap, action="save", bind_password="", allowed_group="st-hk-users"))
    ok("Zugangsgruppe: Mitglied darf", login("st-ldap2", LPW)[1] == 303)
    ok("Zugangsgruppe: Nicht-Mitglied abgewiesen", login("st-outsider", LPW)[1] == 401 and auth.get_user("st-outsider") is None)
    lc.modify(f"cn=st-hk-users,ou=groups,{BASE_DN}", {"member": [(MODIFY_DELETE, [f"uid=st-ldap2,ou=people,{BASE_DN}"])]})
    auth._settings_cache["v"] = None                      # Einstellungen hat der Server geaendert
    ok("Aus Zugangsgruppe entfernt -> Erneuerung verweigert", auth.user_info("st-ldap2", fresh=True) is None)
    adm.post("/settings/ldap", dict(ldap, action="save", bind_password="", enabled=""))

    # --- SSO / OIDC ---------------------------------------------------------------------------------
    oidc = {"enabled": "1", "issuer": "http://mockoidc:8080/default", "client_id": "saga", "client_secret": "test-secret",
            "scopes": "openid profile email", "username_claim": "preferred_username", "name_claim": "name", "email_claim": "email",
            "groups_claim": "groups", "label": "Mit Test-SSO anmelden", "auto_create": "1", "verify_tls": "1"}
    adm.post("/settings/oidc", dict(oidc, action="test"))
    html = adm.req("GET", "/settings/oidc")[2]
    ok("OIDC-Test: Discovery gelesen, Issuer passt", "✓ Discovery" in html and "✓ Issuer" in html)
    adm.post("/settings/oidc", dict(oidc, action="save"))
    ok("SSO-Knopf auf der Anmeldeseite", "Mit Test-SSO anmelden" in main_client().req("GET", "/login")[2])
    c, s, loc = sso_login("sso-sub-1", {"preferred_username": "st-sso", "name": "Sso Sina", "email": "sina@example.org", "groups": ["s_st-wiki"]})
    ok(f"SSO-Anmeldung ({s} {str(loc)[:60]})", s == 303 and loc == "/")
    i = auth.user_info("st-sso", fresh=True)
    ok("SSO-Konto angelegt mit Name/E-Mail/Gruppen", i and i["source"] == "oidc" and i["name"] == "Sso Sina" and "s_st-wiki" in i["groups"])
    ok("Dienst ueber SSO-Gruppe sichtbar", any(x["name"] == "St Wiki" for x in c.api("GET", "/api/services")[1]))
    c2, s, html = sso_login("sso-sub-2", {"preferred_username": "st-local", "name": "Fremd"})
    ok("SSO uebernimmt kein lokales Konto mit gleichem Namen", s == 403 and "bereits" in html and auth.get_user("st-local")["source"] == "local")
    adm.post("/settings/oidc", dict(oidc, action="save", client_secret="", allowed_group="st-hk-users"))
    _, s, html = sso_login("sso-sub-3", {"preferred_username": "st-sso2", "groups": ["andere"]})
    ok("SSO-Zugangsgruppe: ohne Gruppe abgewiesen", s == 403 and auth.get_user("st-sso2") is None)
    _, s, loc = sso_login("sso-sub-3", {"preferred_username": "st-sso2", "groups": ["/st-hk-users"]})
    ok("SSO-Zugangsgruppe: Mitglied (Keycloak-Pfad /gruppe) darf", s == 303)
    s, loc, _ = c.req("GET", "/logout")
    ok(f"Abmelden leitet zum Anbieter ({loc[:50]})", s == 303 and loc.startswith("http://mockoidc:8080/") and "id_token_hint" in loc)
    ok("Sitzung beendet", c.api("GET", "/api/account")[0] == 401)
    # App-Anmeldung ueber SSO (Mac/iOS/Linux: /app/login -> Anmeldeseite -> SSO -> /app/handoff)
    a = main_client()
    s, loc, _ = a.req("GET", "/app/login?scheme=saga&challenge=" + "A" * 43)
    s2, loc2, _ = a.req("GET", loc)
    a2, s3, loc3 = sso_login("sso-sub-1", {"preferred_username": "st-sso", "groups": ["s_st-wiki", "st-hk-users"]}, start=a)
    s4, loc4, h4 = a.req("GET", loc3) if s3 == 303 else (0, "", "")
    ok(f"SSO-App-Anmeldung: frisch ueber SSO angemeldet -> ohne Rueckfrage bis saga://auth ({loc}, {loc3}, {loc4[:24]})",
       s4 == 303 and loc4.startswith("saga://auth?code="))
    s5, _, _ = a.req("GET", "/app/login?scheme=saga&challenge=" + "B" * 43)
    s5, loc5, h5 = a.req("GET", "/app/handoff")
    m5 = re.search(r'/app/handoff\?go=[A-Za-z0-9_-]+', h5 or "")
    ok("Zweite App-Anmeldung mit bestehender SSO-Sitzung: erst Rueckfrage", s5 == 200 and m5)
    s5, loc5, _ = a.req("GET", m5.group(0)) if m5 else (0, "", "")
    ok(f"... dann Code ({loc5[:24]})", loc5.startswith("saga://auth?code="))
    _, s, _ = sso_login("sso-sub-fremd", {"preferred_username": "st-sso", "groups": ["s_st-wiki", "st-hk-users"]})
    ok(f"SSO: gleicher Benutzername, andere Kennung (sub) -> abgewiesen ({s})", s == 403)
    # Gruppen von SSO-Konten aus LDAP (Keycloak mit FreeIPA-Anbindung), LDAP-Passwort-Anmeldung aus
    adm.post("/settings/oidc", dict(oidc, action="save", client_secret="", allowed_group=""))
    adm.post("/settings/ldap", dict(ldap, action="save", bind_password="", password_login="", sso_groups="1"))
    adm.post("/settings", {"title": "Saga", "group_prefix": "s_", "default_groups": "", "allowed_hosts": ""})
    ok("Nur SSO: ohne Passwort-Login leitet die Anmeldeseite direkt zum Anbieter",
       main_client().req("GET", "/login")[0] == 303 and login("st-ldap", LPW)[1] != 303)
    _, s, loc = sso_login("sso-ldap-1", {"preferred_username": "st-ssoldap", "groups": []})
    i = auth.user_info("st-ssoldap", fresh=True) if auth.get_user("st-ssoldap") else None
    ok(f"SSO-Konto bekommt Gruppen aus LDAP ({s}, {sorted(i['groups']) if i else '-'})", s == 303 and i and "s_st-wiki" in i["groups"])
    _, s, html = sso_login("sso-ldap-2", {"preferred_username": "st-nirgends", "groups": ["s_st-wiki"]})
    ok("SSO-Konto, das es im Verzeichnis nicht gibt -> abgewiesen", s == 403 and auth.get_user("st-nirgends") is None)
    lc.modify(f"cn=s_st-wiki,ou=groups,{BASE_DN}", {"member": [(MODIFY_DELETE, [f"uid=st-ssoldap,ou=people,{BASE_DN}"])]})
    auth._settings_cache["v"] = None
    i = auth.user_info("st-ssoldap", fresh=True)
    ok("Erneuerung liest LDAP-Gruppen neu (Gruppe entzogen)", i and "s_st-wiki" not in i["groups"])
    adm.post("/settings/ldap", dict(ldap, action="save", bind_password="", enabled=""))
    adm.post("/settings/oidc", dict(oidc, action="save", client_secret="", enabled=""))
    adm.post("/settings", {"title": "Saga", "local_login": "1", "group_prefix": "s_", "default_groups": "", "allowed_hosts": ""})
    ok("SSO aus -> kein Knopf", "/login/sso" not in main_client().req("GET", "/login")[2])


if __name__ == "__main__":
    snap = snapshot_settings()
    try:
        main()
    except Exception as ex:  # noqa: BLE001
        import traceback; traceback.print_exc(); ok(f"Abbruch: {ex}", False)
    finally:
        cleanup()
        restore_settings(snap)
    print(f"\nSSO-LDAP {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if res and all(res) else 1)
