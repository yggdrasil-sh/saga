"""Mehrsprachigkeit: Sprachwahl (Konto > Sprach-Cookie > Server-Standard > Accept-Language > Englisch), vollstaendige Kataloge
(gleiche Schluessel und Platzhalter in de/en/es/ru/th), Pluralformen (ru: 1 / 2-4 / 5+), Seiten und Admin-Bereich rendern
in allen Sprachen.
Wegwerf-Konten sp-*, raeumt auf. Umgebung wie tests/standalone.py."""
import http.client, importlib.util, json, os, re, sys, time
import auth
from db import connect
from saga import i18n
from standalone import AD, B, HOST, PW, Client, main_client, ok, res, restore_settings, snapshot_settings

HERE = os.path.dirname(os.path.abspath(__file__))
LANG_RE = re.compile(r'<html lang="([a-z]+)"')
WORDS = {"de": "Anmelden", "en": "Sign in", "es": "Iniciar sesión", "ru": "Войти", "th": "เข้าสู่ระบบ"}


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
    # Sprachumschalter (Cookie): schlaegt Server-Standard und Browser, das Konto schlaegt das Cookie
    ok("Cookie schlaegt Server-Standard und Browser", i18n.resolve("", "es", "de", "en") == "en")
    ok("Konto schlaegt Cookie", i18n.resolve("de", "", "es", "en") == "de")
    ok("Cookie-Kopfzeile lesen", i18n.cookie_from_header('a=1; saga_lang=es; saga=xyz') == "es"
       and i18n.cookie_from_header("saga_lang=fr") == "" and i18n.cookie_from_header("") == "")
    # Kataloge: alle Schluessel in allen Sprachen, gleiche Platzhalter, nichts Unbenutztes (tools/i18n.py check)
    spec = importlib.util.spec_from_file_location("i18n_tool", os.path.join(HERE, "..", "tools", "i18n.py"))
    tool = importlib.util.module_from_spec(spec); spec.loader.exec_module(tool)
    probs = tool.problems()
    for p in probs[:10]:
        print("     ", p)
    keys = tool.used_keys()
    ok(f"Kataloge vollstaendig ({len(keys)} Schluessel, {len(probs)} Probleme)", not probs and len(keys) > 500)
    ok("Gleiche Schluessel in allen Sprachen", all(set(i18n.CATALOG[x]) == set(i18n.CATALOG["de"]) for x in i18n.LANGS))
    # Thai und Russisch (2.x): Erkennung, Namen in der eigenen Sprache, Pluralformen, Datum
    ok("Sprachen de/en/es/ru/th", i18n.LANGS == ("de", "en", "es", "ru", "th"))
    ok("Accept-Language th-TH / th / ru-RU / ru -> th, th, ru, ru",
       [i18n.from_header(h) for h in ("th-TH,th;q=0.9,en;q=0.5", "th", "ru-RU,ru;q=0.9", "ru")] == ["th", "th", "ru", "ru"])
    ok("Accept-Language fr, ru;q=0.5 -> ru", i18n.resolve("", "", "fr-FR, ru;q=0.5, en;q=0.3") == "ru")
    ok("Sprachnamen in der eigenen Sprache", i18n.NAMES["ru"] == "Русский" and i18n.NAMES["th"] == "ไทย")
    ok("Thai: gregorianischer Kalender (th-TH-u-ca-gregory)", i18n.LOCALES["th"] == "th-TH-u-ca-gregory")
    forms = [i18n.t_lang("ru", "{n} neue Nachrichten", n=n) for n in (1, 2, 5, 11, 12, 21, 22, 25, 111)]
    ok(f"Russisch Pluralformen 1/2/5/11/12/21/22/25/111 ({forms[0]}, {forms[1]}, {forms[2]})", forms == [
        "1 новое сообщение", "2 новых сообщения", "5 новых сообщений", "11 новых сообщений", "12 новых сообщений",
        "21 новое сообщение", "22 новых сообщения", "25 новых сообщений", "111 новых сообщений"])
    ok("Russisch Plural in Satz (Verb + Nomen)", i18n.t_lang("ru", "{n} Aufgaben übernommen.", n=3) == "Добавлены 3 задачи."
       and i18n.t_lang("ru", "{n} Aufgaben übernommen.", n=21) == "Добавлена 21 задача.")
    ok("Thai ohne Pluralformen", i18n.t_lang("th", "{n} neue Nachrichten", n=1) == "1 ข้อความใหม่"
       and i18n.t_lang("th", "{n} neue Nachrichten", n=7) == "7 ข้อความใหม่")
    ok("Pluralregeln de/en/es unveraendert", [i18n.plural_cat(x, 1) for x in ("de", "en", "es")] == ["one"] * 3
       and i18n.plural_cat("en", 21) == "other" and i18n.plural_cat("ru", 1.5) == "other" and i18n.plural_cat("th", 1) == "other")
    ok("Pluralformen-Text gilt als eigene Meldung", i18n.is_message(i18n.t_lang("ru", "{n} neue Nachrichten", n=4)))
    ok("Ohne Wert bleiben Pluralformen stehen", "{n:" in i18n.fill("{n:a|b|c}", {"x": 1}, "ru"))
    tok = i18n.set_lang("th")
    try:
        ok("Datum Thai gregorianisch (2026, nicht 2569)", i18n.fmt_date(1781000000).endswith("/2026"))
    finally:
        i18n._cur.reset(tok)
    from saga import ai
    ok("KI: Sprachnamen ru/th fuer Antworten und Uebersetzen", ai.LANG_NAMES.get("ru") == "Russian" and ai.LANG_NAMES.get("th") == "Thai"
       and ai.TARGETS.get("ru") == "Russian" and ai.TARGETS.get("th") == "Thai")
    ok("KI: Fragesprache ru/th an der Schrift erkannt", ai.q_lang("Какая погода завтра в Москве?") == "ru"
       and ai.q_lang("พรุ่งนี้อากาศเป็นอย่างไร") == "th" and ai.q_lang("Wie ist das Wetter morgen?") == "de")
    tok = i18n.set_lang("es")
    try:
        ok("t() mit Platzhalter (es)", i18n.t("Höchstens {n} Lesezeichen.", n=5) == "Máximo 5 marcadores.")
    finally:
        i18n._cur.reset(tok)
    # Glossar Teilen/Kommentare (Spanisch, alle Plattformen): Rollenauswahl, Etiketten, Aktion/Zustand "Erledigt"
    es = i18n.CATALOG["es"]
    want = {"Lesen##Rolle": "Ver", "Kommentieren##Rolle": "Comentar", "Bearbeiten##Rolle": "Editar",
            "Du kannst lesen": "Puedes ver", "Du kannst lesen und kommentieren": "Puedes comentar", "Du kannst bearbeiten": "Puedes editar",
            "✓ Erledigt##Aktion": "✓ Resolver", "✓ Erledigt": "✓ Resuelto"}
    bad = {k: es.get(k) for k, v in want.items() if es.get(k) != v}
    ok(f"Spanisch Teilen/Kommentare wie im Glossar ({len(want)} Begriffe)", not bad or print("      ", bad))


def raw(base, path, cookie="", accept="de-DE,de;q=0.9", host=None):
    """GET ohne Client-Logik -> (status, kopfzeilen als Liste, text)"""
    con = http.client.HTTPConnection(base.hostname, base.port, timeout=30)
    h = {"Host": host or base.netloc, "Accept-Language": accept}
    if cookie:
        h["Cookie"] = cookie
    con.request("GET", path, headers=h)
    r = con.getresponse()
    return r.status, r.getheaders(), r.read().decode("utf-8", "replace")


def hdr(headers, name):
    return [v for k, v in headers if k.lower() == name]


def selected(html):
    m = re.search(r'<form class="lang-sw[^"]*".*?</form>', html, re.S)
    if not m:
        return None
    sel = re.search(r'<option value="([a-z]*)"[^>]*selected', m.group(0))
    return sel.group(1) if sel else ""


def switch_tests(u):
    """Sprachumschalter auf der Anmeldeseite (fremder Rechner): Cookie, Vorrang, Ruecksprung, Admin, 404-Seite."""
    # Anmeldeseite: Umschalter vorhanden, Browser de + Cookie en -> englisch
    c = main_client(); c.lang = "de-DE,de;q=0.9"
    s, _, html = c.req("GET", "/login")
    ok("Anmeldeseite: Sprachumschalter mit Automatisch/de/en/es", s == 200 and 'action="/lang"' in html and selected(html) == ""
       and all(f'<option value="{x}" lang="{x}"' in html for x in i18n.LANGS) and "/static/lang.js" in html
       and ">Русский</option>" in html and ">ไทย</option>" in html)
    s, loc, _ = c.req("GET", "/lang?l=en&next=/login")
    ok("Umschalter: 303 zurueck zur Anmeldeseite + Cookie", s == 303 and loc.endswith("/login") and c.ck.get("saga_lang") == "en")
    s, _, html = c.req("GET", "/login")
    ok("Anmeldung in en mit de-Browser (Cookie)", s == 200 and html_lang(html) == "en" and "Sign in" in html and selected(html) == "en")
    st, hs, _ = raw(B, "/lang?l=es&next=/login", host=HOST)
    sc = " ".join(hdr(hs, "set-cookie")).lower()
    ok("Cookie: nur Sprache, HttpOnly, SameSite=Lax, Pfad /, ein Jahr", st == 303 and "saga_lang=es" in sc and "httponly" in sc
       and "samesite=lax" in sc and "path=/" in sc and "max-age=31536000" in sc)
    s, loc, _ = c.req("GET", "/lang?l=th&next=/login")
    s, _, html = c.req("GET", "/login")
    ok("Umschalter Thai: Anmeldeseite auf Thai", s == 200 and html_lang(html) == "th" and WORDS["th"] in html and selected(html) == "th")
    st, _, html = raw(B, "/login", "saga_lang=ru", host=HOST)
    ok("Cookie ru: Anmeldeseite auf Russisch", html_lang(html) == "ru" and WORDS["ru"] in html)
    st, _, html = raw(B, "/app/pair", "saga_lang=es", host=HOST)
    ok("App-Kopplungsseite in Cookie-Sprache (es) mit Umschalter", html_lang(html) == "es" and selected(html) == "es")
    # Ruecksprung nur auf diesen Server
    for bad in ("//evil.example/x", "https://evil.example/", "/\\evil.example", "javascript:alert(1)", "/\r\nSet-Cookie:x"):
        s, loc, _ = main_client().req("GET", "/lang?l=de&next=" + urllib_quote(bad))
        ok(f"Ruecksprung {bad!r} -> /", s == 303 and (loc == "/" or loc.endswith(HOST + "/")) or print("      ", loc))
    s, loc, _ = main_client().req("GET", "/lang?l=de&next=" + urllib_quote("/login?next=/notes&prompt=login"))
    ok("Ruecksprung mit Abfrage bleibt", s == 303 and loc.endswith("/login?next=/notes&prompt=login"))
    # Automatisch: Cookie weg, wieder Browser
    s, _, _ = c.req("GET", "/lang?l=&next=/login")
    ok("Automatisch loescht das Cookie", s == 303 and "saga_lang" not in c.ck)
    ok("Danach wieder Browser-Sprache (de)", html_lang(c.req("GET", "/login")[2]) == "de")
    # Oeffentliche 404-Seite: Cookie gilt, Links statt Skript
    st, _, html = raw(B, "/@sp-niemand/gibts-nicht", "saga_lang=es", host=HOST)
    ok("Oeffentliche 404 in Cookie-Sprache (es) mit Sprach-Links", st == 404 and html_lang(html) == "es" and 'class="pub-lang"' in html
       and "/lang?l=de&amp;next=%2F%40sp-niemand%2Fgibts-nicht" in html and 'hreflang="th"' in html and 'hreflang="ru"' in html)
    st, _, html = raw(B, "/@sp-niemand/gibts-nicht", "saga_lang=ru", host=HOST)
    ok("Oeffentliche 404 auf Russisch", st == 404 and html_lang(html) == "ru" and i18n.t_lang("ru", "Diese Seite gibt es nicht oder sie ist nicht öffentlich.") in html
       or print("      ", st, html_lang(html)))
    # Angemeldet: Konto "automatisch" -> Cookie gilt; feste Konto-Sprache hat Vorrang
    u.api("PUT", "/api/account/lang", {"lang": ""})
    u.lang = "de"; u.ck["saga_lang"] = "en"
    ok("Angemeldet, Konto automatisch: Cookie (en) statt Browser (de)", html_lang(u.req("GET", "/")[2]) == "en")
    u.api("PUT", "/api/account/lang", {"lang": "es"})
    ok("Konto-Sprache (es) hat Vorrang vor dem Cookie (en)", html_lang(u.req("GET", "/")[2]) == "es")
    # Umschalter angemeldet (z. B. Seite "Anmeldung & Sicherheit"): feste Konto-Sprache wird mit geaendert
    s, _, _ = u.req("GET", "/lang?l=de&next=/")
    ok("Umschalter angemeldet aendert die feste Konto-Sprache", s == 303 and u.api("GET", "/api/account")[1].get("lang") == "de"
       and html_lang(u.req("GET", "/")[2]) == "de")
    u.api("PUT", "/api/account/lang", {"lang": ""})
    s, _, _ = u.req("GET", "/lang?l=es&next=/")
    ok("Konto automatisch: Umschalter setzt nur das Cookie", u.api("GET", "/api/account")[1].get("lang") == "" and u.ck.get("saga_lang") == "es")
    u.ck.pop("saga_lang", None)
    # Apps oeffnen Anmeldung/Kopplung mit ?lang=: gilt fuer diese Browser-Sitzung, nach Konto/Umschalter, vor Browser
    a = main_client(); a.lang = "de-DE,de;q=0.9"
    s, loc, _ = a.req("GET", "/app/login?challenge=" + "A" * 43 + "&scheme=saga&lang=es")
    ok("App-Anmeldung ?lang=es: Weiterleitung + Sitzungs-Cookie", s == 303 and "/login" in loc and a.ck.get("saga_lang_app") == "es")
    s, _, html = a.req("GET", "/login?next=/app/handoff")
    ok("Anmeldeseite danach auf Spanisch (Browser de)", s == 200 and html_lang(html) == "es" and selected(html) == "")
    st, hs, html = raw(B, "/login?next=/app/handoff&lang=en", host=HOST)
    sc = " ".join(v for v in hdr(hs, "set-cookie") if v.startswith("saga_lang_app=")).lower()
    ok("?lang=en direkt auf /login: en, Cookie ohne Ablauf (nur Sitzung)", html_lang(html) == "en" and "saga_lang_app=en" in sc
       and "max-age" not in sc and "expires" not in sc)
    st, hs, html = raw(B, "/login?lang=fr", host=HOST)
    ok("Unbekannte Sprache ?lang=fr wird ignoriert", html_lang(html) == "de" and "saga_lang_app" not in " ".join(hdr(hs, "set-cookie")))
    ok("Kopplungsseite ?lang=en", html_lang(raw(B, "/app/pair?c=" + "x" * 30 + "&lang=en", host=HOST)[2]) == "en")
    ok("App-Sprache ?lang=th / ?lang=ru-RU", html_lang(raw(B, "/login?lang=th", host=HOST)[2]) == "th"
       and html_lang(raw(B, "/login?lang=ru-RU", host=HOST)[2]) == "ru")
    ok("Umschalter (Cookie de) schlaegt ?lang=es", html_lang(raw(B, "/login?lang=es", "saga_lang=de", accept="en", host=HOST)[2]) == "de")
    ok("?lang=es schlaegt Server-Standard/Browser nicht ausserhalb der Anmeldung (/@...)",
       html_lang(raw(B, "/@sp-niemand/x?lang=es", host=HOST)[2]) == "de")
    u.api("PUT", "/api/account/lang", {"lang": "en"})
    u.ck["saga_lang_app"] = "es"
    ok("Konto-Sprache (en) schlaegt die App-Sprache (es)", html_lang(u.req("GET", "/security?lang=es")[2]) == "en")
    u.ck.pop("saga_lang_app", None)
    u.api("PUT", "/api/account/lang", {"lang": ""})
    # Admin-Bereich: Umschalter vor und nach der Anmeldung
    adm = Client(AD); adm.lang = "de"
    s, _, html = adm.req("GET", "/login")
    ok("Admin-Anmeldung: Sprachumschalter", s == 200 and 'action="/lang"' in html)
    s, loc, _ = adm.req("GET", "/lang?l=es&next=/login")
    ok("Admin: Umschalter -> Cookie, Anmeldung auf Spanisch", s == 303 and adm.ck.get("saga_lang") == "es"
       and html_lang(adm.req("GET", "/login")[2]) == "es")
    s, _, _ = adm.post("/login", {"username": "sp-admin", "password": PW})
    s, _, html = adm.req("GET", "/")
    ok("Admin angemeldet: Umschalter in der Kopfzeile, Cookie-Sprache", s == 200 and "adm-lang" in html and html_lang(html) == "es")


def urllib_quote(v):
    from urllib.parse import quote
    return quote(v, safe="")


def main():
    cleanup()
    auth.create_user("sp-user", PW, "Sprach Test")
    auth.create_user("sp-admin", PW, "Sprach Admin", is_admin=True)
    auth.save_setting("default_lang", "")
    time.sleep(6)                                       # Einstellungs-Cache der Instanz (5 s)

    # --- Anmeldeseite: Accept-Language, sonst Englisch --------------------------------------------
    for lang, accept in (("de", "de-DE,de;q=0.9"), ("en", "en-US"), ("es", "es-ES,es;q=0.9"), ("ru", "ru-RU,ru;q=0.9"), ("th", "th-TH,th;q=0.9")):
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
    for lang, accept in (("de", "de"), ("en", "en-GB"), ("es", "es"), ("ru", "ru-RU"), ("th", "th")):
        u.lang = accept
        s1, _, h1 = u.req("GET", "/")
        s2, _, h2 = u.req("GET", "/notes")
        s3, _, h3 = u.req("GET", "/notes?embed=1")
        ok(f"Dashboard, Notizen, Peek in {lang}", (s1, s2, s3) == (200, 200, 200) and html_lang(h1) == html_lang(h2) == html_lang(h3) == lang
           and f"/i18n/{lang}.js?v=" in h1 and f"/i18n/{lang}.js?v=" in h2)
        sj, _, js = u.req("GET", f"/i18n/{lang}.js?v={i18n.VERSION}")
        ok(f"Woerterbuch /i18n/{lang}.js", sj == 200 and js.startswith("window.SAGA_I18N=") and (lang == "de" or len(js) > 5000))
    u.lang = "ru"
    ok("Russisch: Dashboard-Texte", i18n.t_lang("ru", "Offene Aufgaben") in u.req("GET", "/")[2])
    s, d = u.api("POST", "/api/tasks", {"text": "  "})
    ok(f"API-Fehlermeldung auf Russisch ({d.get('error') if isinstance(d, dict) else d})", s == 400 and d.get("error") == i18n.t_lang("ru", "leere Aufgabe"))
    u.lang = "th"
    ok("Thai: Dashboard-Texte", i18n.t_lang("th", "Offene Aufgaben") in u.req("GET", "/")[2])
    sj, _, js = u.req("GET", f"/i18n/ru.js?v={i18n.VERSION}")
    ok("Woerterbuch ru enthaelt Pluralformen", "{n:" in js)
    u.lang = "es"
    ok("Spanisch: Dashboard-Texte", "Tareas pendientes" in u.req("GET", "/")[2])
    s, d = u.api("POST", "/api/tasks", {"text": "  "})
    ok(f"API-Fehlermeldung auf Spanisch ({d.get('error') if isinstance(d, dict) else d})", s == 400 and d.get("error") == "tarea vacía")
    ok("Unbekannte Sprache im Woerterbuch -> 404", u.req("GET", "/i18n/fr.js")[0] == 404)

    # --- Konto-Einstellung schlaegt Accept-Language -------------------------------------------------
    s, a = u.api("GET", "/api/account")
    ok("Konto: Sprache automatisch + Auswahl", s == 200 and a.get("lang") == "" and [x["code"] for x in a.get("langs", [])] == ["", "de", "en", "es", "ru", "th"]
       and {x["code"]: x["name"] for x in a["langs"]}.get("th") == "ไทย")
    s, d = u.api("PUT", "/api/account/lang", {"lang": "th"})
    ok("Konto-Sprache Thai", s == 200 and d.get("lang") == "th" and html_lang(u.req("GET", "/")[2]) == "th")
    s, d = u.api("PUT", "/api/account/lang", {"lang": "de"})
    ok("Sprache im Konto auf Deutsch", s == 200 and d.get("lang") == "de")
    u.lang = "es"
    ok("Konto (de) schlaegt Browser (es)", html_lang(u.req("GET", "/")[2]) == "de")
    ok("Konto-Sprache in /api/account", u.api("GET", "/api/account")[1].get("lang") == "de")
    ok("Ungueltige Sprache -> 400", u.api("PUT", "/api/account/lang", {"lang": "fr"})[0] == 400)
    u.api("PUT", "/api/account/lang", {"lang": ""})
    ok("Automatisch -> wieder Browser (es)", html_lang(u.req("GET", "/")[2]) == "es")

    switch_tests(u)

    # --- Standardnamen bei Anfragen ohne Browser-Sprache (API, Apps): Sprache, die die Oberflaeche zuletzt zeigte --
    def view_name(lang_page):
        u.lang = lang_page
        u.req("GET", "/notes")                         # Oberflaeche in dieser Sprache gesehen
        u.lang = ""
        s, d = u.api("POST", "/api/notes", {"title": "DB ohne Sprache", "kind": "database"})
        with connect() as c:
            row = c.execute("SELECT schema FROM note WHERE id=? AND uid='sp-user'", (d.get("id") if isinstance(d, dict) else 0,)).fetchone()
        return json.loads(row[0])["views"][0]["name"] if s == 200 and row else f"- ({s})"
    for lang_page in ("de", "es"):
        got = view_name(lang_page)
        ok(f"Neue DB per API ohne Accept-Language, Oberflaeche {lang_page} -> Ansicht {got!r}", got == i18n.t_lang(lang_page, "Tabelle"))
    with connect() as c:
        n = c.execute("SELECT COUNT(*) FROM user_kv WHERE uid='sp-user' AND k='lang_seen'").fetchone()[0]
    ok("Gesehene Sprache nur einmal gespeichert", n == 1)
    u.api("PUT", "/api/account/lang", {"lang": "en"})
    got = view_name("de")
    ok(f"Konto-Sprache (en) schlaegt gesehene Sprache (de) -> {got!r}", got == "Table")
    u.api("PUT", "/api/account/lang", {"lang": ""})
    u.lang = "es"

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
    for lang, accept in (("de", "de"), ("en", "en"), ("es", "es-AR"), ("ru", "ru-RU"), ("th", "th-TH")):
        adm.lang = accept
        out = {p: adm.req("GET", p) for p in pages}
        bad = [p for p, r in out.items() if r[0] != 200 or html_lang(r[2]) != lang]
        ok(f"Admin-Seiten in {lang} ({len(pages)})", not bad or print("      ", bad))
    # 2.5 (Testrunde Web Nr. 007): Schalter fuer Chat-Stufe 3 mit Erklaerung in allen Sprachen, speichert chat_v3
    lbl = {"de": "Chat-Stufe 3", "en": "Chat level 3", "es": "Nivel de chat 3", "ru": "Уровень чатов 3", "th": "แชทระดับ 3"}
    for lg, w in lbl.items():
        adm.lang = lg
        h = adm.req("GET", "/settings")[2]
        ok(f"Admin Allgemein ({lg}): Schalter Chat-Stufe 3 mit Erklaerung", 'name="chat_v3"' in h and w in h and 'name="chat_v3_days"' in h)
    adm.lang = "de"
    v3_prev = auth.settings(fresh=True).get("chat_v3")
    base_f = {"title": auth.settings(fresh=True).get("title") or "Saga", "local_login": "1", "default_lang": "",
              "group_prefix": auth.settings(fresh=True).get("group_prefix") or ""}
    adm.post("/settings", dict(base_f, chat_v3="1"))
    on = auth.settings(fresh=True).get("chat_v3")
    adm.post("/settings", base_f)
    ok("Admin Allgemein: chat_v3 an/aus speichern", on is True and auth.settings(fresh=True).get("chat_v3") is False)
    auth.save_setting("chat_v3", bool(v3_prev))
    # 2.5 (Nr. 020): korrigierte Grenzen werden gemeldet, nicht still uebernommen
    calls_prev = dict(auth.settings(fresh=True).get("calls") or {})
    s, _, _ = adm.post("/calls", {"action": "limits", "horn_video": "30", "horn_audio": "-5", "share_legs": "0", "share_mbit": "15"})
    h = adm.req("GET", "/calls")[2]
    cc = auth.settings(fresh=True).get("calls") or {}
    ok("Anrufe: Grenzen korrigiert und gemeldet (auf 25/2 begrenzt, 0 -> Standard 10)", s == 303 and "adm-flash warn" in h
       and "auf 25 begrenzt" in h and "auf 2 begrenzt" in h and "Standard 10" in h and cc.get("horn_video") == 25 and cc.get("share_legs") == 10)
    adm.post("/calls", {"action": "limits", "horn_video": "8", "horn_audio": "", "share_legs": "5", "share_mbit": "15"})
    ok("Anrufe: gueltige Grenzen ohne Hinweis", "adm-flash warn" not in adm.req("GET", "/calls")[2])
    auth.save_setting("calls", calls_prev)
    adm.lang = "en"
    ok("Admin Allgemein: Feld Standardsprache", 'name="default_lang"' in adm.req("GET", "/settings")[2] and "Default language" in adm.req("GET", "/settings")[2])
    adm.lang = "es"
    s, _, html = adm.req("GET", "/")
    ok("Admin-Uebersicht spanisch", "Resumen" in html and "Idioma predeterminado" in html)
    s, loc, _ = adm.post("/settings", {"title": auth.settings(fresh=True).get("title") or "Saga", "local_login": "1",
                                       "group_prefix": auth.settings(fresh=True).get("group_prefix") or "", "default_lang": "de",
                                       "update_check": "1" if auth.settings(fresh=True).get("update_check") else ""})
    ok("Standardsprache im Admin speichern", s == 303 and auth.settings(fresh=True).get("default_lang") == "de")
    s, _, html = adm.req("GET", "/settings")
    ok("Admin Standardsprache: Auswahl mit Русский/ไทย", '<option value="ru"' in html and "Русский" in html and "ไทย" in html)
    s, loc, _ = adm.post("/settings", {"title": auth.settings(fresh=True).get("title") or "Saga", "local_login": "1",
                                       "group_prefix": auth.settings(fresh=True).get("group_prefix") or "", "default_lang": "th",
                                       "update_check": "1" if auth.settings(fresh=True).get("update_check") else ""})
    ok("Standardsprache Thai im Admin speichern", s == 303 and auth.settings(fresh=True).get("default_lang") == "th")


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
