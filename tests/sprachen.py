"""Mehrsprachigkeit: Sprachwahl (Konto > Server-Standard > Accept-Language > Englisch), vollstaendige Kataloge
(gleiche Schluessel und Platzhalter in de/en/es), Seiten und Admin-Bereich rendern in allen drei Sprachen.
Wegwerf-Konten sp-*, raeumt auf. Umgebung wie tests/standalone.py."""
import importlib.util, os, re, sys, time
import auth
from db import connect
from saga import i18n
from standalone import AD, PW, Client, main_client, ok, res, restore_settings, snapshot_settings

HERE = os.path.dirname(os.path.abspath(__file__))
LANG_RE = re.compile(r'<html lang="([a-z]+)"')
WORDS = {"de": "Anmelden", "en": "Sign in", "es": "Iniciar sesión"}


def html_lang(html):
    m = LANG_RE.search(html)
    return m.group(1) if m else ""


def cleanup():
    with connect() as c, c:
        for t in ("task", "note", "note_version", "bookmark", "fav", "device_login", "app_token", "user_kv", "immich_key", "cal_account", "file"):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE 'sp-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'sp-%'")
        c.execute("DELETE FROM login_fail WHERE key LIKE 'u:sp-%' OR key LIKE 'ui:sp-%'")


def catalog_tests():
    # Sprachwahl ohne Server
    ok("Accept-Language es-MX -> es", i18n.resolve("", "", "es-MX,es;q=0.9,en;q=0.5") == "es")
    ok("Accept-Language mit q-Werten (fr, de;q=0.5) -> de", i18n.resolve("", "", "fr-FR, de;q=0.5, en;q=0.3") == "de")
    ok("Unbekannte Sprache -> Englisch", i18n.resolve("", "", "fr, it;q=0.8") == "en")
    ok("Ohne Angabe -> Englisch", i18n.resolve("", "", "") == "en")
    ok("Konto schlaegt Server-Standard und Browser", i18n.resolve("de", "es", "en") == "de")
    ok("Server-Standard schlaegt Browser", i18n.resolve("", "es", "de") == "es")
    # Kataloge: alle Schluessel in allen Sprachen, gleiche Platzhalter, nichts Unbenutztes (tools/i18n.py check)
    spec = importlib.util.spec_from_file_location("i18n_tool", os.path.join(HERE, "..", "tools", "i18n.py"))
    tool = importlib.util.module_from_spec(spec); spec.loader.exec_module(tool)
    probs = tool.problems()
    for p in probs[:10]:
        print("     ", p)
    keys = tool.used_keys()
    ok(f"Kataloge vollstaendig ({len(keys)} Schluessel, {len(probs)} Probleme)", not probs and len(keys) > 500)
    ok("Gleiche Schluessel in de/en/es", set(i18n.CATALOG["de"]) == set(i18n.CATALOG["en"]) == set(i18n.CATALOG["es"]))
    tok = i18n.set_lang("es")
    try:
        ok("t() mit Platzhalter (es)", i18n.t("Höchstens {n} Lesezeichen.", n=5) == "Máximo 5 marcadores.")
    finally:
        i18n._cur.reset(tok)


def main():
    cleanup()
    auth.create_user("sp-user", PW, "Sprach Test")
    auth.create_user("sp-admin", PW, "Sprach Admin", is_admin=True)
    auth.save_setting("default_lang", "")
    time.sleep(6)                                       # Einstellungs-Cache der Instanz (5 s)

    # --- Anmeldeseite: Accept-Language, sonst Englisch --------------------------------------------
    for lang, accept in (("de", "de-DE,de;q=0.9"), ("en", "en-US"), ("es", "es-ES,es;q=0.9")):
        c = main_client(); c.lang = accept
        s, _, html = c.req("GET", "/login")
        ok(f"Anmeldeseite {lang}: <html lang> und Text", s == 200 and html_lang(html) == lang and WORDS[lang] in html)
    c = main_client(); c.lang = ""
    s, _, html = c.req("GET", "/login")
    ok("Anmeldeseite ohne Accept-Language -> Englisch", s == 200 and html_lang(html) == "en" and "Sign in" in html)
    c = main_client(); c.lang = "fr-FR,fr;q=0.9"
    ok("Anmeldeseite Franzoesisch -> Englisch", html_lang(c.req("GET", "/login")[2]) == "en")

    # --- Angemeldet: alle Seiten in allen Sprachen ------------------------------------------------
    u = main_client()
    s, loc, _ = u.post("/login", {"username": "sp-user", "password": PW})
    ok("Anmeldung sp-user", s == 303)
    for lang, accept in (("de", "de"), ("en", "en-GB"), ("es", "es")):
        u.lang = accept
        s1, _, h1 = u.req("GET", "/")
        s2, _, h2 = u.req("GET", "/notes")
        s3, _, h3 = u.req("GET", "/notes?embed=1")
        ok(f"Dashboard, Notizen, Peek in {lang}", (s1, s2, s3) == (200, 200, 200) and html_lang(h1) == html_lang(h2) == html_lang(h3) == lang
           and f"/i18n/{lang}.js?v=" in h1 and f"/i18n/{lang}.js?v=" in h2)
        sj, _, js = u.req("GET", f"/i18n/{lang}.js?v={i18n.VERSION}")
        ok(f"Woerterbuch /i18n/{lang}.js", sj == 200 and js.startswith("window.SAGA_I18N=") and (lang == "de" or len(js) > 5000))
    u.lang = "es"
    ok("Spanisch: Dashboard-Texte", "Tareas pendientes" in u.req("GET", "/")[2])
    s, d = u.api("POST", "/api/tasks", {"text": "  "})
    ok(f"API-Fehlermeldung auf Spanisch ({d.get('error') if isinstance(d, dict) else d})", s == 400 and d.get("error") == "tarea vacía")
    ok("Unbekannte Sprache im Woerterbuch -> 404", u.req("GET", "/i18n/fr.js")[0] == 404)

    # --- Konto-Einstellung schlaegt Accept-Language -------------------------------------------------
    s, a = u.api("GET", "/api/account")
    ok("Konto: Sprache automatisch + Auswahl", s == 200 and a.get("lang") == "" and [x["code"] for x in a.get("langs", [])] == ["", "de", "en", "es"])
    s, d = u.api("PUT", "/api/account/lang", {"lang": "de"})
    ok("Sprache im Konto auf Deutsch", s == 200 and d.get("lang") == "de")
    u.lang = "es"
    ok("Konto (de) schlaegt Browser (es)", html_lang(u.req("GET", "/")[2]) == "de")
    ok("Konto-Sprache in /api/account", u.api("GET", "/api/account")[1].get("lang") == "de")
    ok("Ungueltige Sprache -> 400", u.api("PUT", "/api/account/lang", {"lang": "fr"})[0] == 400)
    u.api("PUT", "/api/account/lang", {"lang": ""})
    ok("Automatisch -> wieder Browser (es)", html_lang(u.req("GET", "/")[2]) == "es")

    # --- Standardsprache des Servers (Admin -> Allgemein) -------------------------------------------
    auth.save_setting("default_lang", "es")
    time.sleep(6)
    c = main_client(); c.lang = "de"
    ok("Server-Standard (es) schlaegt Browser (de) auf der Anmeldeseite", html_lang(c.req("GET", "/login")[2]) == "es")
    u.api("PUT", "/api/account/lang", {"lang": "en"})
    ok("Konto (en) schlaegt Server-Standard (es)", html_lang(u.req("GET", "/")[2]) == "en")
    auth.save_setting("default_lang", "")
    time.sleep(6)

    # --- Admin-Bereich in allen Sprachen -------------------------------------------------------------
    adm = Client(AD); adm.lang = "de"
    s, _, _ = adm.post("/login", {"username": "sp-admin", "password": PW})
    ok("Admin-Anmeldung", s == 303)
    pages = ["/", "/users", "/groups", "/services", "/settings", "/settings/oidc", "/settings/ldap"]
    uid = auth.get_user("sp-user")["id"]
    pages.append(f"/users/{uid}")
    for lang, accept in (("de", "de"), ("en", "en"), ("es", "es-AR")):
        adm.lang = accept
        out = {p: adm.req("GET", p) for p in pages}
        bad = [p for p, r in out.items() if r[0] != 200 or html_lang(r[2]) != lang]
        ok(f"Admin-Seiten in {lang} ({len(pages)})", not bad or print("      ", bad))
    adm.lang = "en"
    ok("Admin Allgemein: Feld Standardsprache", 'name="default_lang"' in adm.req("GET", "/settings")[2] and "Default language" in adm.req("GET", "/settings")[2])
    adm.lang = "es"
    s, _, html = adm.req("GET", "/")
    ok("Admin-Uebersicht spanisch", "Resumen" in html and "Idioma predeterminado" in html)
    s, loc, _ = adm.post("/settings", {"title": auth.settings(fresh=True).get("title") or "Saga", "local_login": "1",
                                       "group_prefix": auth.settings(fresh=True).get("group_prefix") or "", "default_lang": "de",
                                       "update_check": "1" if auth.settings(fresh=True).get("update_check") else ""})
    ok("Standardsprache im Admin speichern", s == 303 and auth.settings(fresh=True).get("default_lang") == "de")


if __name__ == "__main__":
    catalog_tests()
    snap = snapshot_settings()
    try:
        main()
    finally:
        cleanup()
        restore_settings(snap)
    print(f"\nSPRACHEN {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if res and all(res) else 1)
