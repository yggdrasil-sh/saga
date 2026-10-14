"""Veroeffentlichen (1.2): oeffentlicher Name (Vorschlag, Regeln, eindeutig), /@name/<slug> ohne Anmeldung,
Unterseiten nur mit children und nur wirklich darunter, geloescht/archiviert/abgeschaltet -> 404 (neutral), Bilder nur
aus der Seite, XSS raus, CSP ohne Skript, noindex als Standard, Verweise auf private Seiten als Text, Datenbanken,
Startseite fuer Besucher, fremde Seiten, Admin-Zurueckziehen, Konto loeschen, /@name (Profil/Liste).
Wegwerf-Konten pv-*, raeumt auf. Umgebung wie die anderen Tests (HK_TEST_BASE, HK_TEST_ADMIN, BASE_URL, SESSION_SECRET)."""
import base64, http.client, io, json, os, sys, time, urllib.parse
from itsdangerous import TimestampSigner
from PIL import Image
import auth
from db import connect

B = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
AD = urllib.parse.urlparse(os.environ.get("HK_TEST_ADMIN", "http://127.0.0.1:8098"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
PW = "Publish-Pass-123"
WAIT = 5.6                       # Einstellungen sind im Server 5 s zwischengespeichert
res = []


def ok(n, c):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, cookie=None, body=None, hdr=None, raw_body=None, lang="de"):
    """-> (status, kopfzeilen (klein), daten: JSON, Text oder Bytes)"""
    c = http.client.HTTPConnection(B.hostname, B.port, timeout=30)
    h = {"Host": HOST, "Accept-Language": lang}
    if cookie:
        h["Cookie"] = "saga=" + cookie; h["X-Saga"] = "1"
    if body is not None:
        h["Content-Type"] = "application/json"; raw_body = json.dumps(body)
    h.update(hdr or {})
    c.request(m, p, body=raw_body, headers=h)
    r = c.getresponse(); data = r.read()
    hd = {k.lower(): v for k, v in r.getheaders()}
    ct = hd.get("content-type", "")
    if "json" in ct:
        try:
            data = json.loads(data)
        except ValueError:
            pass
    elif ct.startswith("text/"):
        data = data.decode("utf-8", "replace")
    return r.status, hd, data


def upload(cookie, data, name):
    bd = "pv-test"
    mp = (f"--{bd}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\nContent-Type: image/png\r\n\r\n").encode() \
        + data + f"\r\n--{bd}--\r\n".encode()
    s, _, d = req("POST", "/api/files", cookie, raw_body=mp, hdr={"Content-Type": f"multipart/form-data; boundary={bd}"})
    return d["file"]["url"]


def png(color):
    b = io.BytesIO(); Image.new("RGB", (800, 400), color).save(b, "PNG"); return b.getvalue()


def setting(k, v):
    auth.save_setting(k, v)


def cleanup():
    with connect() as c, c:
        for t in ("task", "note", "note_version", "user_kv", "file", "note_publish", "public_name"):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE 'pv-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'pv-%'")


def blocks(*bl):
    return json.dumps({"blocks": list(bl)})


def main():
    cleanup()
    prev = auth.settings(fresh=True)
    prev_en, prev_home = prev.get("publish_enabled", True), prev.get("public_home", 0)
    setting("publish_enabled", True); setting("public_home", 0)
    auth.create_user("pv-a", PW, "Änne Müller")
    auth.create_user("pv-b", PW, "Bob")
    auth.create_user("pv-adm", PW, "Admin", is_admin=True)
    A, Bk = ck("pv-a"), ck("pv-b")
    time.sleep(WAIT)

    # --- Oeffentlicher Name ----------------------------------------------------------------------------------
    s, _, d = req("GET", "/api/account/public-name", A)
    ok(f"Vorschlag aus dem Anzeigenamen, nie der Anmeldename ({d.get('suggested')})",
       s == 200 and d["name"] is None and d["suggested"].startswith("aenne") and "pv-a" not in d["suggested"])
    for bad in ("admin", "api", "login", "well-known", "a", "-abc", "Äpfel", "x" * 33, "ab cd", "saga"):
        s, _, d = req("PUT", "/api/account/public-name", A, {"name": bad, "check": True})
        ok(f"Name '{bad[:12]}' abgelehnt", s == 200 and d["ok"] is False and d.get("error"))
        s, _, d = req("PUT", "/api/account/public-name", A, {"name": bad})
        ok(f"Speichern von '{bad[:12]}' -> {s}", s in (400, 409))
    s, _, d = req("PUT", "/api/account/public-name", A, {"name": "pv-anne", "check": True})
    ok("Pruefen: gueltig und frei", s == 200 and d["ok"] is True)
    s, _, d = req("PUT", "/api/account/public-name", A, {"name": "PV-Anne"})
    ok(f"Gespeichert (klein geschrieben): {d.get('name')}", s == 200 and d["name"] == "pv-anne")
    s, _, d = req("PUT", "/api/account/public-name", Bk, {"name": "pv-anne"})
    ok(f"Name eindeutig: anderes Konto -> {s}", s == 409)
    s, _, d = req("PUT", "/api/account/public-name", Bk, {"name": "pv-anne", "check": True})
    ok("Pruefen meldet vergeben", d["ok"] is False)
    s, _, d = req("PUT", "/api/account/public-name", A, {"name": "pv-anne"}, hdr={"Content-Type": "text/plain"})
    ok(f"Nur JSON ({s})", s == 415)
    s, _, _ = req("GET", "/api/account/public-name", None)
    ok("Ohne Anmeldung -> 401", s == 401)

    # --- Seiten anlegen -----------------------------------------------------------------------------------------
    nid = lambda d: d["id"]
    root = nid(req("POST", "/api/notes", A, {"title": "Mein Garten", "icon": "🌱"})[2])
    child = nid(req("POST", "/api/notes", A, {"title": "Beete", "parent_id": root})[2])
    grand = nid(req("POST", "/api/notes", A, {"title": "Tomaten", "parent_id": child})[2])
    priv = nid(req("POST", "/api/notes", A, {"title": "Geheimes Tagebuch"})[2])
    other = nid(req("POST", "/api/notes", A, {"title": "Über mich"})[2])
    bnote = nid(req("POST", "/api/notes", Bk, {"title": "Bobs Seite"})[2])
    dbid = nid(req("POST", "/api/notes", A, {"title": "Pflanzen", "kind": "database", "parent_id": root})[2])
    rose = nid(req("POST", "/api/notes", A, {"title": "Rose", "parent_id": dbid, "props": {"status": "done", "datum": "2026-10-01"}})[2])
    nid(req("POST", "/api/notes", A, {"title": "Minze", "parent_id": dbid, "props": {"status": "todo"}})[2])
    img1, img2 = upload(A, png((200, 40, 40)), "rot.png"), upload(A, png((40, 40, 200)), "blau.png")
    fid1, fid2 = img1.rsplit("/", 1)[1], img2.rsplit("/", 1)[1]
    body = blocks(
        {"type": "header", "data": {"text": "Willkommen", "level": 2}},
        {"type": "paragraph", "data": {"text": f'Siehe <a href="/notes#{child}">Beete</a> und <a href="/notes#{priv}">Tagebuch</a> und '
                                                f'<a href="https://example.org/x?a=1&amp;b=2">extern</a> <b>fett</b>'}},
        {"type": "paragraph", "data": {"text": '<script>alert(1)</script><img src=x onerror="alert(2)"><a href="javascript:alert(3)">js-link</a>'
                                                '<a href="data:text/html,x">data-link</a><span onclick="x()" style="color:red">span</span>'}},
        {"type": "paragraph", "data": {"text": '<A HREF="JaVaScRiPt:alert(5)">jsU</A><a href="jav&#x09;ascript:alert(6)">jsT</a>'
                                                '<svg><a xlink:href="javascript:alert(7)">svgA</a></svg><a href="https://x.org/" '
                                                'onmouseover="alert(8)">hov</a><math><style><img src=x onerror=alert(10)></style></math>'
                                                '<time datetime="2026-01-01&quot; onx=1">zeit</time>'}},
        {"type": "list", "data": {"style": "unordered", "items": [{"content": "eins", "items": [{"content": "eins-a", "items": []}]}]}},
        {"type": "list", "data": {"style": "checklist", "items": [{"content": "giessen", "meta": {"checked": True}, "items": []}]}},
        {"type": "list", "data": {"style": "ordered", "meta": {"start": 3}, "items": [{"content": "drei", "items": []}]}},
        {"type": "quote", "data": {"text": "Zitat", "caption": "Quelle"}},
        {"type": "code", "data": {"code": "<b>kein html</b>", "language": "python"}},
        {"type": "delimiter", "data": {}},
        {"type": "image", "data": {"file": {"url": img1}, "caption": "Rote Rose"}},
        {"type": "table", "data": {"withHeadings": True, "content": [["Kopf", "B"], ["x", "y"]]}},
        {"type": "columns", "data": {"cols": [{"blocks": [{"type": "paragraph", "data": {"text": "links"}}]},
                                              {"blocks": [{"type": "paragraph", "data": {"text": "rechts"}}]}]}},
        {"type": "callout", "data": {"emoji": "⚠️", "text": "Achtung"}},
        {"type": "toggle", "data": {"title": "Mehr", "open": False, "blocks": [{"type": "paragraph", "data": {"text": "versteckt"}}]}},
        {"type": "embed", "data": {"url": "https://www.youtube.com/watch?v=abc", "kind": "youtube"}},
        {"type": "embed", "data": {"url": "javascript:alert(4)"}},
        {"type": "tasks", "data": {}},
        {"type": "calendar", "data": {}},
        {"type": "immich", "data": {}},
        {"type": "attaches", "data": {"file": {"url": img2, "name": "geheim.png"}}},
        {"type": "database", "data": {"db": dbid}},
        {"type": "unbekannt", "data": {"text": "UNBEKANNT-BLOCK"}},
    )
    with connect() as c, c:                        # direkt (ohne Bereinigung beim Speichern): prueft die Ausgabe-Bereinigung
        c.execute("UPDATE note SET body=?, format='blocks' WHERE id=?", (body, root))
        c.execute("UPDATE note SET body=?, format='blocks' WHERE id=?", (blocks({"type": "paragraph", "data": {"text": "Tomatenwissen"}},
                                                                                {"type": "image", "data": {"file": {"url": img2}}}), grand))

    # --- Veroeffentlichen per API ----------------------------------------------------------------------------
    s, _, d = req("GET", f"/api/notes/{root}/publish", A)
    ok(f"Zustand vorher: nicht veroeffentlicht, Vorschlag {d.get('slug')}", s == 200 and d["published"] is False and d["slug"] == "mein-garten")
    s, _, d = req("PUT", f"/api/notes/{bnote}/publish", Bk, {})
    ok(f"Ohne oeffentlichen Namen -> {s} need_name", s == 409 and d.get("need_name") and d.get("suggested_name"))
    s, _, d = req("PUT", f"/api/notes/{root}/publish", Bk, {})
    ok(f"Fremde Seite veroeffentlichen -> {s}", s in (403, 404))
    ok("Fremde Seite: Zustand -> 404", req("GET", f"/api/notes/{root}/publish", Bk)[0] == 404)
    ok("Fremde Seite: beenden -> 404", req("DELETE", f"/api/notes/{root}/publish", Bk)[0] == 404)
    s, _, d = req("PUT", f"/api/notes/{root}/publish", A, {"slug": "mein-garten"}, hdr={"Content-Type": "text/plain"})
    ok(f"Nur JSON ({s})", s == 415)
    s, _, d = req("PUT", f"/api/notes/{root}/publish", A, {})
    url = d.get("url") or ""
    ok(f"Veroeffentlicht: {url}", s == 200 and d["published"] and url.endswith("/@pv-anne/mein-garten") and d["children"] is False
       and d["indexable"] is False)
    d = req("GET", f"/api/notes/{root}", A)[2]
    ok("Seite meldet published + public_url", d.get("published") is True and str(d.get("public_url", "")).endswith("/@pv-anne/mein-garten")
       and d.get("published_via") is None)
    d = req("GET", f"/api/notes/{child}", A)[2]
    ok("Unterseite ohne children: nicht oeffentlich, kein published_via", d.get("published") is False and d.get("published_via") is None)
    P = "/@pv-anne/mein-garten"

    # --- Oeffentlicher Abruf ------------------------------------------------------------------------------------
    s, h, html = req("GET", P)
    ok(f"Anonym veroeffentlicht -> {s}", s == 200 and "<h1>Mein Garten</h1>" in html and "Willkommen" in html)
    csp = h.get("content-security-policy", "")
    ok(f"CSP ohne Skripte ({csp[:60]}…)", "script-src 'none'" in csp and "unsafe" not in csp and "default-src 'none'" in csp)
    ok("noindex als Standard (Kopfzeile + meta)", "noindex" in h.get("x-robots-tag", "") and 'content="noindex' in html)
    ok(f"Kurzes Cache-Control ({h.get('cache-control')})", "max-age=60" in h.get("cache-control", ""))
    ok("Kein Sitzungs-Cookie fuer Besucher", "set-cookie" not in h)
    ok("Kein JavaScript in der Seite", "<script" not in html.lower() and "onerror" not in html and "onclick" not in html
       and "javascript:" not in html and "data:text" not in html and "style=" not in html)
    ok("Text der entfernten Links bleibt", "js-link" in html and "data-link" in html and "span" in html)
    ok("Fiese Eingaben: Grossschreibung, Tab/Entity im Schema, SVG/MathML, Ereignis-Attribute, time-Attribut",
       all(f"alert({i})" not in html for i in (5, 6, 7, 8, 10)) and "<svg" not in html and "<math" not in html
       and "jsU" in html and "jsT" in html and "svgA" in html and "hov" in html and "<time>zeit</time>" in html)
    ok("Externer Link mit rel nofollow", 'href="https://example.org/x?a=1&amp;b=2" rel="noopener noreferrer nofollow"' in html)
    ok("Verweis auf private Seite = Text ohne Link", "Tagebuch" in html and f"#{priv}" not in html and f"/{priv}" not in html)
    ok("Verweis auf Unterseite ohne children = Text", "Beete" in html and f"{P}/{child}" not in html)
    ok("Keine internen Verweise/IDs", "/notes" not in html and "/files/" not in html and f"/{root}" not in html)
    ok("Bloecke: Ueberschrift, Liste verschachtelt, Checkliste, nummeriert ab 3",
       "<h3>Willkommen</h3>" in html and "eins-a" in html and 'type="checkbox" disabled checked' in html and '<ol start="3">' in html)
    ok("Zitat, Code escaped, Trenner, Tabelle, Spalten, Hinweis, Ausklappbar",
       "<blockquote>" in html and "&lt;b&gt;kein html&lt;/b&gt;" in html and "<hr>" in html and "<th>Kopf</th>" in html
       and 'class="pub-cols n2"' in html and "pub-callout k1" in html and "<details" in html and "versteckt" in html)
    ok("Einbettung nur als Link (YouTube), javascript-Einbettung entfaellt",
       'href="https://www.youtube.com/watch?v=abc" rel="noopener noreferrer nofollow"' in html and "<iframe" not in html)
    ok("Aufgaben/Kalender/Immich/Dateianhang/unbekannt entfallen", "UNBEKANNT-BLOCK" not in html and "geheim.png" not in html
       and fid2 not in html)
    ok("Bild mit Unterschrift ueber die oeffentliche Adresse", f'{P}/f/{fid1}?w=1280' in html and "<figcaption>Rote Rose</figcaption>" in html)
    ok("Datenbank als Tabelle (Titel + Eigenschaften), Eintraege ohne children nicht verlinkt",
       "Pflanzen" in html and "Rose" in html and "Minze" in html and "Erledigt" in html and "01.10.2026" in html
       and f"{P}/{rose}" not in html)
    ok("Erstellt mit Saga", "Erstellt mit Saga" in html and "yggdrasil-sh.github.io/saga" in html)
    s, _, html_en = req("GET", P, lang="en")
    ok("Sprache des Besuchers (en)", s == 200 and "Made with Saga" in html_en and 'lang="en"' in html_en)
    # Design-Runde (13/14/22): Inhalt in der Kontosprache, Bedienung in der Sprache des Besuchers; Absenderzeile; Leiste
    ok("Absenderzeile unter dem Titel: @name · aktualisiert <Datum>", '<p class="pub-by" lang="de"><a href="/@pv-anne">@pv-anne</a> · aktualisiert <time datetime="'
       in html and '<p class="pub-by" lang="en"><a href="/@pv-anne">@pv-anne</a> · updated <time datetime="' in html_en)
    ok("Bedienleiste: links @name (Link zum Profil), ohne 'Anmelden' ausserhalb der Startseite",
       '<a class="pub-brand" href="/@pv-anne">@pv-anne</a>' in html and "pub-login" not in html)
    ok("Häkchen wirken nicht bedienbar (disabled + aria-disabled)", 'type="checkbox" disabled checked aria-disabled="true"' in html)
    s, _, d = req("PUT", "/api/account/lang", A, {"lang": "es"})
    s2, _, html_lang = req("GET", P, lang="en")
    ok(f"Inhalt in der Kontosprache (es), Bedienung beim Besucher (en) ({s})", s == 200 and '<html lang="en">' in html_lang
       and '<article class="pub-page" lang="es">' in html_lang and "Made with Saga" in html_lang and " · updated " in html_lang)
    req("PUT", "/api/account/lang", A, {"lang": ""})
    s, h, _ = req("GET", P, A)
    ok(f"Angemeldet (Set-Cookie moeglich): nur private Zwischenspeicherung ({h.get('cache-control')})",
       s == 200 and "private" in h.get("cache-control", "") and "public" not in h.get("cache-control", ""))
    s, h, data = req("HEAD", P)
    ok(f"HEAD wie GET -> {s} {h.get('content-type')}", s == 200 and h.get("content-type", "").startswith("text/html")
       and not data and "script-src 'none'" in h.get("content-security-policy", ""))
    ok("HEAD auf Unbekanntes -> 404", req("HEAD", "/@pv-niemand/x")[0] == 404 and req("HEAD", "/@pv-anne/gibt-es-nicht")[0] == 404)

    # --- Bilder ---------------------------------------------------------------------------------------------------
    s, h, data = req("GET", f"{P}/f/{fid1}")
    ok(f"Benutztes Bild -> {s} {h.get('content-type')}", s == 200 and h.get("content-type") == "image/png" and data[:4] == b"\x89PNG")
    s, h, data = req("GET", f"{P}/f/{fid1}?w=320")
    ok(f"Verkleinert -> {s} {h.get('content-type')}", s == 200 and h.get("content-type") in ("image/webp", "image/png"))
    ok("Bild aus Dateianhang/anderer Seite -> 404", req("GET", f"{P}/f/{fid2}")[0] == 404)
    ok("Unsinnige Datei-ID -> 404", req("GET", f"{P}/f/../../etc")[0] == 404 and req("GET", f"{P}/f/xyz")[0] == 404)
    s, h, _ = req("HEAD", f"{P}/f/{fid1}")
    ok(f"HEAD Bild -> {s}", s == 200 and h.get("content-type") == "image/png" and h.get("x-content-type-options") == "nosniff")
    s, h, _ = req("GET", f"{P}/f/{fid1}", A)
    ok(f"Bild mit Cookie: private ({h.get('cache-control')})", s == 200 and h.get("cache-control", "").startswith("private"))

    # --- 404 neutral ---------------------------------------------------------------------------------------------
    s1, h1, nf1 = req("GET", "/@pv-anne/geheimes-tagebuch")
    s2, _, nf2 = req("GET", "/@gibt-es-nicht/mein-garten")
    s3, _, nf3 = req("GET", "/@pv-anne/mein-garten/abc")
    s4, _, nf4 = req("GET", "/@pv-anne/mein-garten/1/2/3")
    ok(f"Unveroeffentlicht/unbekannt/Unsinn -> {s1} {s2} {s3} {s4}, immer gleiche Seite",
       (s1, s2, s3, s4) == (404, 404, 404, 404) and nf1 == nf2 == nf3 == nf4 and "Nicht gefunden" in nf1)
    # P10: Grossbuchstaben und "/" am Ende -> 301 auf die kleingeschriebene Adresse ohne "/" (rein syntaktisch)
    for src, dst in (("/@PV-ANNE/Mein-Garten", "/@pv-anne/mein-garten"), ("/@pv-anne/mein-garten/", "/@pv-anne/mein-garten"),
                     ("/@PV-Anne", "/@pv-anne"), ("/@pv-anne/", "/@pv-anne"), ("/@Pv-Anne/Mein-Garten/", "/@pv-anne/mein-garten"),
                     ("/@gibt-es-nicht/Xy", "/@gibt-es-nicht/xy")):
        s, h, _ = req("GET", src)
        ok(f"301 {src} -> {h.get('location')}", s == 301 and h.get("location") == dst)
    ok("Umleitung fuehrt zur Seite", req("GET", "/@PV-ANNE/Mein-Garten/")[0] == 301 and req("GET", "/@pv-anne/mein-garten")[0] == 200)
    # Umleitungen bleiben immer auf diesem Server unter /@ (kein offenes Umleiten, keine Kopfzeilen-Injektion, keine Schleife)
    for src in ("/@PV-ANNE/Mein-Garten/%0d%0aSet-Cookie:x=1", "/@PV-ANNE/%2F%2Fevil.example/", "/@PV-ANNE/x/%5C%5Cevil.example",
                "/@%E2%84%AAV-anne/mein-garten", "/@PV-ANNE/Mein-Garten?next=//evil.example", "/@Pv-Anne/Mein-Garten/1/"):
        s, h, _ = req("GET", src)
        loc = h.get("location") or ""
        ok(f"Umleitung sicher: {src[:40]} -> {s} {loc[:60]}", "set-cookie" not in h and (s != 301 or (loc.startswith("/@") and not loc.startswith("//")
           and "\r" not in loc and "\n" not in loc and req("GET", loc.split("?")[0])[0] != 301)))
    ok("Unsinn mit Grossbuchstaben bleibt neutrale 404", req("GET", "/@%C3%84PFEL/x")[2] == nf1 and req("GET", "/@A/x")[0] == 404)
    ok("404: noindex, no-store, CSP", "noindex" in h1.get("x-robots-tag", "") and h1.get("cache-control") == "no-store"
       and "script-src 'none'" in h1.get("content-security-policy", ""))
    login = os.environ["BASE_URL"].rstrip("/") + "/login"
    ok("404 mit nächstem Schritt: ohne Startseite -> 'Anmelden'", f'<a class="pub-next" href="{login}">Anmelden</a>' in nf1
       and "Zur Startseite" not in nf1)

    # --- Unterseiten -----------------------------------------------------------------------------------------------
    ok("Unterseite ohne children -> 404", req("GET", f"{P}/{child}")[0] == 404)
    rev0 = req("GET", f"/api/notes/{root}", A)[2]["rev"]
    s, _, d = req("PUT", f"/api/notes/{root}/publish", A, {"children": True})
    ok("children an", s == 200 and d["children"] is True)
    ok("Veroeffentlichen aendert die Seite nicht (rev gleich)", req("GET", f"/api/notes/{root}", A)[2]["rev"] == rev0)
    # P1: Unterseite (und Enkel) melden, dass sie ueber die Elternseite oeffentlich sind
    for x in (child, grand):
        d = req("GET", f"/api/notes/{x}", A)[2]
        v = d.get("published_via") or {}
        ok(f"published_via in /api/notes/{x}: {v.get('title')}", d.get("published") is False and v.get("note_id") == root
           and v.get("title") == "Mein Garten" and str(v.get("url", "")).endswith(f"{P}/{x}"))
        v2 = req("GET", f"/api/notes/{x}/publish", A)[2].get("published_via") or {}
        ok("published_via auch im Zustand der Veroeffentlichung", v2 == v)
    ok("Private Seite: kein published_via", req("GET", f"/api/notes/{priv}/publish", A)[2].get("published_via") is None)
    s, _, html = req("GET", f"{P}/{child}")
    ok(f"Unterseite mit children -> {s}, Pfad zurueck", s == 200 and "<h1>Beete</h1>" in html and f'href="{P}"' in html)
    s, _, html = req("GET", f"{P}/{grand}")
    ok(f"Enkel -> {s}", s == 200 and "Tomatenwissen" in html and f"{P}/{grand}/f/{fid2}" in html)
    ok("Bild der Unterseite nur unter ihrer Adresse", req("GET", f"{P}/{grand}/f/{fid2}")[0] == 200 and req("GET", f"{P}/f/{fid2}")[0] == 404
       and req("GET", f"{P}/{child}/f/{fid2}")[0] == 404)
    ok("Fremde Seite als Unterseiten-ID -> 404", req("GET", f"{P}/{bnote}")[0] == 404)
    ok("Eigene, aber nicht darunterliegende Seite -> 404", req("GET", f"{P}/{priv}")[0] == 404 and req("GET", f"{P}/{other}")[0] == 404)
    ok("Seite selbst als Unterseiten-ID -> 404", req("GET", f"{P}/{root}")[0] == 404)
    ok("Unsinnige IDs -> 404", req("GET", f"{P}/0")[0] == 404 and req("GET", f"{P}/99999999999999999999")[0] == 404)
    s, _, html = req("GET", P)
    ok("Jetzt: Verweis auf Unterseite verlinkt, Unterseiten-Liste, Datenbank-Eintraege verlinkt",
       f'href="{P}/{child}"' in html and "Unterseiten" in html and f'href="{P}/{rose}"' in html and f"#{priv}" not in html)
    with connect() as c, c:
        c.execute("UPDATE note SET icon=? WHERE id=?", (img2, child))
    s, _, html = req("GET", P)
    ok("Bild-Symbol einer Unterseite: Adresse unter der Unterseite, dort erreichbar",
       f'src="{P}/{child}/f/{fid2}?w=160"' in html and req("GET", f"{P}/{child}/f/{fid2}?w=160")[0] == 200)
    with connect() as c, c:
        c.execute("UPDATE note SET icon='' WHERE id=?", (child,))
    s, _, html = req("GET", f"{P}/{rose}")
    ok("Datenbank-Eintrag mit Eigenschaften", s == 200 and "<h1>Rose</h1>" in html and "pub-props" in html and "Erledigt" in html)

    # --- Vorlagen (is_template) bleiben privat, auch unter einer Seite mit children -------------------------------
    tpl = nid(req("POST", "/api/notes", A, {"title": "PV-Seitenvorlage", "parent_id": root})[2])
    dtpl = nid(req("POST", "/api/notes", A, {"title": "PV-DB-Vorlage", "parent_id": dbid})[2])
    tchild = nid(req("POST", "/api/notes", A, {"title": "PV-In-Vorlage", "parent_id": tpl})[2])
    for x in (tpl, dtpl):
        req("PATCH", f"/api/notes/{x}", A, {"template": True})
    s, _, html = req("GET", P)
    ok("Vorlagen nicht in Unterseiten-Liste und nicht als Datenbank-Eintrag", s == 200 and "PV-Seitenvorlage" not in html
       and "PV-DB-Vorlage" not in html and "Rose" in html)
    ok("Vorlage (und Seite darin) als Unterseiten-Adresse -> 404", req("GET", f"{P}/{tpl}")[0] == 404 and req("GET", f"{P}/{dtpl}")[0] == 404
       and req("GET", f"{P}/{tchild}")[0] == 404)
    s, _, _ = req("PUT", f"/api/notes/{tpl}/publish", A, {"slug": "pv-vorlage"})
    ok(f"Vorlage veroeffentlichen -> abgelehnt ({s})", s == 400 and req("GET", "/@pv-anne/pv-vorlage")[0] == 404)
    for x in (tchild, tpl, dtpl):
        req("DELETE", f"/api/notes/{x}", A)

    # --- Titel mit HTML: im <title>, og:title und h1 escaped --------------------------------------------------------
    xss = nid(req("POST", "/api/notes", A, {"title": "xss"})[2])
    with connect() as c, c:                        # direkt, ohne Bereinigung beim Speichern
        c.execute("UPDATE note SET title=?, icon=? WHERE id=?", ('"><script>alert(9)</script>', '"><b>x</b>', xss))
    s, _, d = req("PUT", f"/api/notes/{xss}/publish", A, {"slug": "xss-titel"})
    s, _, html = req("GET", "/@pv-anne/xss-titel")
    ok(f"Titel/Symbol escaped -> {s}", s == 200 and "<script>alert(9)" not in html and "&lt;script&gt;alert(9)" in html
       and 'content="&#34;&gt;&lt;script' in html.replace("&quot;", "&#34;") and "<b>x</b>" not in html)
    req("DELETE", f"/api/notes/{xss}/publish", A)

    # --- Archiv, Papierkorb ------------------------------------------------------------------------------------------
    req("POST", f"/api/notes/{child}/archive", A, {"archived": True})
    ok("Archivierte Unterseite (samt Enkel) -> 404", req("GET", f"{P}/{child}")[0] == 404 and req("GET", f"{P}/{grand}")[0] == 404)
    req("POST", f"/api/notes/{child}/archive", A, {"archived": False})
    ok("Aus dem Archiv -> wieder 200", req("GET", f"{P}/{grand}")[0] == 200)
    req("DELETE", f"/api/notes/{root}", A)
    ok("Seite im Papierkorb -> 404 (auch Unterseiten, Bilder)", req("GET", P)[0] == 404 and req("GET", f"{P}/{child}")[0] == 404
       and req("GET", f"{P}/f/{fid1}")[0] == 404)
    with connect() as c:
        ok("Veroeffentlichung bleibt gespeichert", c.execute("SELECT 1 FROM note_publish WHERE note_id=?", (root,)).fetchone() is not None)
    req("POST", f"/api/trash/{root}/restore", A)
    ok("Wiederhergestellt -> wieder oeffentlich", req("GET", P)[0] == 200)
    req("POST", f"/api/notes/{root}/archive", A, {"archived": True})
    ok("Archivierte Seite -> 404", req("GET", P)[0] == 404)
    req("POST", f"/api/notes/{root}/archive", A, {"archived": False})

    # --- Suchmaschinen, Adresse, Profil ---------------------------------------------------------------------------
    s, _, d = req("PUT", f"/api/notes/{root}/publish", A, {"indexable": True})
    s, h, html = req("GET", P)
    ok("indexable: kein noindex, canonical", "x-robots-tag" not in h and 'content="index, follow"' in html and 'rel="canonical"' in html)
    base = os.environ["BASE_URL"].rstrip("/")
    s, h, html = req("GET", P, hdr={"X-Forwarded-Host": "fremd.example.org"})
    ok("canonical bleibt BASE_URL bei fremdem Host (nicht in PUBLIC_HOSTS)", f'href="{base}{P}"' in html and "fremd.example.org" not in html)
    s, _, d = req("PUT", f"/api/notes/{other}/publish", A, {"slug": "mein-garten"})
    ok(f"Adresse doppelt -> {s}", s == 409)
    s, _, d = req("PUT", f"/api/notes/{other}/publish", A, {"slug": "Ungültig!"})
    ok(f"Ungueltige Adresse -> {s}", s == 400)
    s, _, d = req("PUT", f"/api/notes/{other}/publish", A, {"profile": True})
    ok(f"Zweite Seite: Adresse aus dem Titel ({d.get('slug')}), Profilseite", s == 200 and d["slug"] == "ueber-mich" and d["profile"])
    s, _, html = req("GET", "/@pv-anne")
    ok("/@name zeigt die Profilseite", s == 200 and "<h1>Über mich</h1>" in html)
    s, _, d = req("PUT", f"/api/notes/{root}/publish", A, {"profile": True})
    ok("Hoechstens eine Profilseite", req("GET", f"/api/notes/{other}/publish", A)[2]["profile"] is False)
    s, _, html = req("GET", "/@pv-anne")
    ok("Profil gewechselt", "<h1>Mein Garten</h1>" in html)
    req("PUT", f"/api/notes/{root}/publish", A, {"profile": False})
    s, _, html = req("GET", "/@pv-anne")
    ok("Ohne Profilseite: Liste der oeffentlichen Seiten", s == 200 and "Mein Garten" in html and "Über mich" in html
       and "Geheimes Tagebuch" not in html and "Bobs" not in html)
    ok("/@unbekannt -> 404", req("GET", "/@pv-niemand")[0] == 404)
    req("PUT", f"/api/notes/{root}/publish", A, {"profile": True})
    s, _, d = req("PUT", f"/api/notes/{other}/publish", A, {"profile": True, "indexable": True})
    ok(f"Profilseite ersetzt eine andere -> Hinweis ({(d.get('replaced_profile') or {}).get('title')})", s == 200
       and (d.get("replaced_profile") or {}).get("note_id") == root)
    s, _, d = req("GET", f"/api/notes/{root}/publish", A)
    ok("Zustand nennt die bisherige Profilseite", (d.get("profile_other") or {}).get("note_id") == other)
    req("DELETE", f"/api/notes/{other}/publish", A)
    ok("Beenden wirkt sofort", req("GET", "/@pv-anne/ueber-mich")[0] == 404 and req("GET", "/@pv-anne")[2].find("Über mich</h1>") < 0)
    # P2: Beenden merkt Adresse + Einstellungen; wieder veroeffentlichen = wie vorher
    s, _, d = req("GET", f"/api/notes/{other}/publish", A)
    ok(f"Nach dem Beenden: gemerkt ({d.get('slug')}, profile={d.get('profile')}, withdrawn_by={d.get('withdrawn_by')})",
       d["published"] is False and d["url"] is None and d["slug"] == "ueber-mich" and d["profile"] is True and d["indexable"] is True
       and d["withdrawn_by"] == "user" and d["withdrawn_at"])
    s, _, d = req("PUT", f"/api/notes/{root}/publish", A, {"slug": "ueber-mich"})
    ok(f"Beendete Adresse bleibt fuer ihre Seite reserviert ({s})", s == 409)
    s, _, d3 = req("POST", "/api/notes", A, {"title": "Über mich"})
    s, _, d = req("GET", f"/api/notes/{d3['id']}/publish", A)
    ok(f"Vorschlag bei doppelter Adresse mit Bindestrich ({d.get('slug')})", d["slug"] == "ueber-mich-2")
    s, _, d = req("PUT", f"/api/notes/{other}/publish", A, {})
    ok(f"Wieder veroeffentlicht: gleiche Adresse und Einstellungen ({d.get('url')})", s == 200 and d["published"]
       and d["url"].endswith("/@pv-anne/ueber-mich") and d["profile"] is True and d["indexable"] is True and d["withdrawn_by"] is None
       and req("GET", "/@pv-anne/ueber-mich")[0] == 200)
    # P3: Adresse aendern - alte Adresse 404, neue 200
    s, _, d = req("PUT", f"/api/notes/{other}/publish", A, {"slug": "ueber-anne"})
    ok("Adresse geaendert: neue 200, alte 404", s == 200 and d["url"].endswith("/@pv-anne/ueber-anne")
       and req("GET", "/@pv-anne/ueber-anne")[0] == 200 and req("GET", "/@pv-anne/ueber-mich")[0] == 404)
    req("PUT", f"/api/notes/{other}/publish", A, {"profile": False})
    req("DELETE", f"/api/notes/{other}/publish", A)
    # Beendete Veroeffentlichung (active=0) ist nirgends mehr oeffentlich: kein Verweisziel, nicht in der Liste,
    # keine Startseite - auch wenn die Einstellung noch auf sie zeigt
    req("PUT", f"/api/notes/{priv}/publish", A, {"slug": "tagebuch"})
    ok("Aktiv: Verweis wird Link und steht in der Liste", 'href="/@pv-anne/tagebuch"' in req("GET", P)[2]
       and "/@pv-anne/tagebuch" in req("GET", "/@pv-anne")[2])
    req("DELETE", f"/api/notes/{priv}/publish", A)
    s, _, html = req("GET", P)
    ok("Beendet: Verweis wieder Text, nicht in der Liste, Adresse 404", 'href="/@pv-anne/tagebuch"' not in html and "Tagebuch" in html
       and "/@pv-anne/tagebuch" not in req("GET", "/@pv-anne")[2] and req("GET", "/@pv-anne/tagebuch")[0] == 404)
    setting("public_home", priv); time.sleep(WAIT)
    s, h, _ = req("GET", "/")
    ok(f"Startseite zeigt auf beendete Veroeffentlichung -> Anmeldung ({s})", s == 303 and h.get("location") == "/login")
    setting("public_home", 0); time.sleep(WAIT)

    # --- Namen aendern -------------------------------------------------------------------------------------------------
    req("PUT", "/api/account/public-name", A, {"name": "pv-anne2"})
    ok("Neuer Name: alte Adresse 404, neue 200", req("GET", P)[0] == 404 and req("GET", "/@pv-anne2/mein-garten")[0] == 200)
    req("PUT", "/api/account/public-name", A, {"name": "pv-anne"})

    # --- Abschalten (Admin) -------------------------------------------------------------------------------------------------
    setting("publish_enabled", False); time.sleep(WAIT)
    ok("Veroeffentlichen abgeschaltet -> 404 (Seite, Unterseite, Bild, Profil)", req("GET", P)[0] == 404 and req("GET", f"{P}/{child}")[0] == 404
       and req("GET", f"{P}/f/{fid1}")[0] == 404 and req("GET", "/@pv-anne")[0] == 404)
    s, _, d = req("PUT", f"/api/notes/{other}/publish", A, {})
    ok(f"Abgeschaltet: Veroeffentlichen -> {s}", s == 403)
    ok("Zustand meldet enabled=false", req("GET", f"/api/notes/{root}/publish", A)[2]["enabled"] is False)
    setting("publish_enabled", True); time.sleep(WAIT)
    ok("Wieder erlaubt -> 200", req("GET", P)[0] == 200)

    # --- Startseite fuer Besucher ---------------------------------------------------------------------------------------
    s, h, _ = req("GET", "/")
    ok(f"Ohne Startseite: / -> {s} {h.get('location')}", s == 303 and h.get("location") == "/login")
    setting("public_home", root); time.sleep(WAIT)
    s, h, html = req("GET", "/")
    ok(f"Startseite fuer Besucher -> {s}", s == 200 and "<h1>Mein Garten</h1>" in html and "Anmelden" in html and "/login" in html
       and "no-cache" in h.get("cache-control", "") and "script-src 'none'" in h.get("content-security-policy", ""))
    ok("Startseite: links der Name der Instanz, 'Anmelden' als Textlink", '<a class="pub-brand" href="/">' in html and 'class="pub-login"' in html)
    s, _, nf = req("GET", "/@pv-niemand/x")
    ok("404 mit nächstem Schritt: mit Startseite -> 'Zur Startseite'", s == 404 and '<a class="pub-next" href="/">Zur Startseite</a>' in nf
       and req("GET", "/@pv-niemand/x", lang="es")[2].find(">Ir a la página de inicio</a>") > 0)
    s, h, data = req("HEAD", "/")
    ok(f"HEAD / fuer Besucher -> {s}", s == 200 and not data and "no-cache" in h.get("cache-control", ""))
    s, h, html = req("GET", "/", A)
    ok(f"Angemeldet: weiter das Dashboard ({s})", s == 200 and "pub-main" not in html and "Mein Garten</h1>" not in html)
    setting("publish_enabled", False); time.sleep(WAIT)
    ok("Abgeschaltet: / -> Anmeldung", req("GET", "/")[0] == 303)
    setting("publish_enabled", True); time.sleep(WAIT)

    # --- Admin: Liste, Startseite, Zurueckziehen ----------------------------------------------------------------------------
    from standalone import Client
    adm = Client(AD)
    ok("Admin-Anmeldung", adm.post("/login", {"username": "pv-adm", "password": PW})[0] == 303)
    s, _, page = adm.req("GET", "/published")
    ok("Admin: Liste der oeffentlichen Seiten", s == 200 and "@pv-anne" in page and "Mein Garten" in page and "Zurückziehen" in page
       and "data-confirm" in page and "Startseite für Besucher" in page)
    s, _, page = adm.req("GET", "/settings")
    ok("Admin Allgemein: Schalter + Auswahl der Startseite", 'name="publish_enabled"' in page and f'value="{root}" selected' in page)
    s, _, _ = adm.post("/published", {"action": "withdraw", "id": str(root)})
    ok(f"Admin zieht zurueck -> {s}, sofort 404", s == 303 and req("GET", P)[0] == 404 and req("GET", f"{P}/{child}")[0] == 404)
    s, _, d = req("GET", f"/api/notes/{root}/publish", A)
    ok("Benutzer sieht: vom Admin zurueckgezogen", d["published"] is False and d["withdrawn_by"] == "admin" and d["children"] is True)
    ok("Unterseite danach ohne published_via", req("GET", f"/api/notes/{child}", A)[2].get("published_via") is None)
    s, _, page = adm.req("GET", "/published")
    ok("Admin-Liste zeigt nur aktive Veroeffentlichungen", "Mein Garten" not in page)
    ok("Startseite war diese Seite -> geleert", int(auth.settings(fresh=True).get("public_home") or 0) == 0)
    time.sleep(WAIT)
    ok("/ fuer Besucher wieder Anmeldung", req("GET", "/")[0] == 303)

    # --- Endgueltig loeschen, Konto loeschen ----------------------------------------------------------------------------------
    req("PUT", f"/api/notes/{priv}/publish", A, {})
    req("DELETE", f"/api/notes/{priv}", A)
    req("DELETE", f"/api/trash/{priv}", A)
    with connect() as c:
        ok("Endgueltig geloescht -> Veroeffentlichung entfernt", c.execute("SELECT 1 FROM note_publish WHERE note_id=?", (priv,)).fetchone() is None)
    req("PUT", f"/api/notes/{root}/publish", A, {"children": True})
    ok("Vor dem Loeschen des Kontos erreichbar", req("GET", P)[0] == 200)
    from saga.userdata import USER_TABLES, delete_user_data
    ok("Tabellen in USER_TABLES", "note_publish" in USER_TABLES and "public_name" in USER_TABLES)
    delete_user_data("pv-a")
    with connect() as c, c:
        c.execute("DELETE FROM users WHERE username='pv-a'")
        left = c.execute("SELECT (SELECT COUNT(*) FROM note_publish WHERE uid='pv-a') + (SELECT COUNT(*) FROM public_name WHERE uid='pv-a')").fetchone()[0]
    ok("Konto geloescht -> Adressen weg", left == 0 and req("GET", P)[0] == 404 and req("GET", "/@pv-anne")[0] == 404)

    # --- Rate-Limit (eigene Adresse ueber X-Forwarded-For, der Test-Server vertraut 127.0.0.1) ------------------------------
    xff = {"X-Forwarded-For": "203.0.113.%d" % (int(time.time()) % 200 + 20)}
    codes = [req("GET", "/@pv-niemand/x", hdr=xff)[0] for _ in range(125)]
    s, h, html = req("GET", "/@pv-niemand/x", hdr=xff)
    ok(f"Rate-Limit: nach {codes.count(404)} Aufrufen 429 mit Retry-After, neutral", codes[:100].count(404) == 100 and s == 429
       and h.get("retry-after") == "60" and "Zu viele Anfragen" in html and "script-src 'none'" in h.get("content-security-policy", ""))
    ok("Andere Adressen sind nicht betroffen", req("GET", "/@pv-niemand/x")[0] == 404)

    setting("publish_enabled", prev_en); setting("public_home", prev_home)
    cleanup()
    print(f"\nVEROEFFENTLICHEN {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        cleanup()
        raise
