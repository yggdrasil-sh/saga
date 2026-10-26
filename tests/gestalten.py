"""Seite gestalten (2.5, G1-G3, G9, G10): Bereinigung der neuen Felder (Spaltenbreiten, Bildbreite/-ausrichtung,
Block-Farbe tunes.hkstyle, Toggle-Ueberschrift - auch verschachtelt), PATCH/GET font/small, Duplizieren,
Veroeffentlichen (nur Klassen aus festen Werten, keine fremden Werte, alte Seiten unveraendert) und Markdown.
Wegwerf-Konto gs-a, raeumt auf. Umgebung wie die anderen Tests (HK_TEST_BASE, BASE_URL, SESSION_SECRET)."""
import base64, http.client, io, json, os, re, sys, time, urllib.parse, zipfile
from itsdangerous import TimestampSigner
from PIL import Image
import auth
from db import connect

B = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
res = []
n500 = [0]


def ok(n, c, info=None):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, "" if c or info is None else f"-> {info}")


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, cookie=None, body=None, raw_body=None, hdr=None):
    c = http.client.HTTPConnection(B.hostname, B.port, timeout=30)
    h = {"Host": HOST, "Accept-Language": "de"}
    if cookie:
        h["Cookie"] = "saga=" + cookie; h["X-Saga"] = "1"
    if body is not None:
        h["Content-Type"] = "application/json"; raw_body = json.dumps(body)
    h.update(hdr or {})
    c.request(m, p, body=raw_body, headers=h)
    r = c.getresponse(); data = r.read()
    if r.status >= 500:
        n500[0] += 1
    ct = r.getheader("content-type", "")
    if "json" in ct:
        try:
            data = json.loads(data)
        except ValueError:
            pass
    elif ct.startswith("text/"):
        data = data.decode("utf-8", "replace")
    return r.status, {k.lower(): v for k, v in r.getheaders()}, data


def cleanup():
    with connect() as c, c:
        for t in ("note", "note_version", "user_kv", "file", "note_publish", "public_name"):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE 'gs-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'gs-%'")


P = lambda t, **kw: dict({"type": "paragraph", "data": {"text": t}}, **kw)


def save(A, nid, blocks):
    s, _, d = req("PATCH", f"/api/notes/{nid}", A, {"body": json.dumps({"blocks": blocks}), "format": "blocks"})
    assert s == 200, (s, d)
    return json.loads(req("GET", f"/api/notes/{nid}", A)[2]["body"])["blocks"]


def main():
    cleanup()
    auth.create_user("gs-a", "Gestalten-Pass-123", "Gestalterin")
    A = ck("gs-a")
    nid = req("POST", "/api/notes", A, {"title": "Gestalten"})[2]["id"]
    # eigenes Bild (Seiten nehmen nur in Saga hochgeladene Bilder)
    bimg = io.BytesIO(); Image.new("RGB", (400, 200), "teal").save(bimg, "PNG")
    bd = "gs-test"
    mp = (f"--{bd}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"a.png\"\r\nContent-Type: image/png\r\n\r\n").encode() \
        + bimg.getvalue() + f"\r\n--{bd}--\r\n".encode()
    s, _, up = req("POST", "/api/files", A, raw_body=mp, hdr={"Content-Type": f"multipart/form-data; boundary={bd}"})
    url = up["file"]["url"]

    # --- G1: Spaltenbreiten -------------------------------------------------------------------------------------
    got = save(A, nid, [
        {"type": "columns", "data": {"cols": [{"w": 3, "blocks": [P("a")]}, {"w": 1, "blocks": [P("b")]}]}},
        {"type": "columns", "data": {"cols": [{"w": 0.9, "blocks": []}, {"w": 0.05, "blocks": []}, {"w": 0.05, "blocks": []}]}},
        {"type": "columns", "data": {"cols": [{"w": 1, "blocks": []}, {"blocks": []}]}},
        {"type": "columns", "data": {"cols": [{"w": "1fr", "blocks": []}, {"w": -2, "blocks": []}]}},
        {"type": "columns", "data": {"cols": [{"w": True, "blocks": []}, {"w": 1, "blocks": []}]}},
    ])
    w = [[c.get("w") for c in b["data"]["cols"]] for b in got]
    ok(f"G1: Gewichte auf Summe 1 normiert ({w[0]})", w[0] == [0.75, 0.25])
    ok(f"G1: Mindestbreite 0,15 je Spalte, Summe 1 ({w[1]})", w[1] == [0.7, 0.15, 0.15] and abs(sum(w[1]) - 1) < 1e-9)
    ok(f"G1: Spalte ohne w -> alle w entfernt ({w[2]})", w[2] == [None, None])
    ok(f"G1: ungueltige w (Text, negativ, bool) -> entfernt ({w[3]}, {w[4]})", w[3] == [None, None] and w[4] == [None, None])

    # --- G2: Bild/Embed Breite und Ausrichtung ------------------------------------------------------------------
    got = save(A, nid, [
        {"type": "image", "data": {"file": {"url": url}, "width": 250, "align": "left"}},
        {"type": "image", "data": {"file": {"url": url}, "width": 3.4, "align": "middle"}},
        {"type": "image", "data": {"file": {"url": url}, "width": "50%", "align": "<b>"}},
        {"type": "image", "data": {"file": {"url": url}, "width": 42.6}},
        {"type": "embed", "data": {"url": "https://www.youtube.com/watch?v=abcdefghijk", "kind": "youtube", "width": 60, "align": "right"}},
        {"type": "embed", "data": {"url": "https://example.org/seite", "width": 60, "align": "right"}},
    ])
    d = [b["data"] for b in got]
    ok(f"G2: width auf 100 begrenzt, align left bleibt ({d[0].get('width')}, {d[0].get('align')})", d[0].get("width") == 100 and d[0].get("align") == "left")
    ok(f"G2: width mindestens 10, align unbekannt -> weg ({d[1]})", d[1].get("width") == 10 and "align" not in d[1])
    ok("G2: width als Text / align mit HTML -> entfernt", "width" not in d[2] and "align" not in d[2])
    ok(f"G2: width gerundet auf ganze Zahl ({d[3].get('width')})", d[3].get("width") == 43 and isinstance(d[3]["width"], int))
    ok("G2: YouTube-Player behaelt width/align", d[4].get("width") == 60 and d[4].get("align") == "right")
    ok("G2: Link-Karte (Embed ohne Player) bekommt keine Breite", "width" not in d[5] and "align" not in d[5])

    # --- G3: Block-Farbe (tunes.hkstyle) --------------------------------------------------------------------------
    got = save(A, nid, [
        P("rot", tunes={"hkstyle": {"c": "red"}}),
        P("blau bg", tunes={"hkstyle": {"c": "blue", "bg": True}, "fremd": {"x": 1}}),
        P("hex", tunes={"hkstyle": {"c": "#ff0000"}}),
        P("css", tunes={"hkstyle": {"c": "red;background:url(x)"}}),
        P("bg text", tunes={"hkstyle": {"c": "green", "bg": "true", "z": 1}}),
        P("nur fremd", tunes={"textVariant": "call-out"}),
        P("kaputt", tunes="hkstyle"),
        {"type": "code", "data": {"code": "x"}, "tunes": {"hkstyle": {"c": "red"}}},
        {"type": "image", "data": {"file": {"url": url}}, "tunes": {"hkstyle": {"c": "red"}}},
        {"type": "header", "data": {"text": "H", "level": 2}, "tunes": {"hkstyle": {"c": "purple"}}},
        {"type": "list", "data": {"style": "unordered", "items": []}, "tunes": {"hkstyle": {"c": "pink", "bg": True}}},
        {"type": "quote", "data": {"text": "q"}, "tunes": {"hkstyle": {"c": "gray"}}},
        {"type": "callout", "data": {"text": "c"}, "tunes": {"hkstyle": {"c": "yellow", "bg": True}}},
        {"type": "toggle", "data": {"title": "t", "blocks": [P("innen", tunes={"hkstyle": {"c": "orange"}, "x": 1}),
                                                            P("innen2", tunes={"hkstyle": {"c": "teal"}})]}, "tunes": {"hkstyle": {"c": "brown"}}},
        {"type": "columns", "data": {"cols": [{"blocks": [P("spalte", tunes={"hkstyle": {"c": "green", "bg": True}, "y": 2})]},
                                              {"blocks": [{"type": "code", "data": {"code": "c"}, "tunes": {"hkstyle": {"c": "red"}}}]}]},
         "tunes": {"hkstyle": {"c": "red"}}},
    ])
    tn = [b.get("tunes") for b in got]
    ok("G3: Textfarbe bleibt", tn[0] == {"hkstyle": {"c": "red"}})
    ok("G3: Hintergrund bleibt, fremde Tunes entfernt", tn[1] == {"hkstyle": {"c": "blue", "bg": True}})
    ok("G3: Hex-/CSS-Werte -> tunes ganz weg", tn[2] is None and tn[3] is None)
    ok(f"G3: bg nur echtes true, fremde Schluessel in hkstyle weg ({tn[4]})", tn[4] == {"hkstyle": {"c": "green"}})
    ok("G3: nur fremde bzw. kaputte tunes -> entfernt", tn[5] is None and tn[6] is None)
    ok("G3: Farbe an Code/Bild (nicht erlaubter Typ) -> entfernt", tn[7] is None and tn[8] is None)
    ok("G3: Ueberschrift, Liste, Zitat, Hinweis, Toggle behalten Farbe",
       tn[9] == {"hkstyle": {"c": "purple"}} and tn[10] == {"hkstyle": {"c": "pink", "bg": True}} and tn[11] == {"hkstyle": {"c": "gray"}}
       and tn[12] == {"hkstyle": {"c": "yellow", "bg": True}} and tn[13] == {"hkstyle": {"c": "brown"}})
    inner = got[13]["data"]["blocks"]
    ok(f"G3: verschachtelt im Toggle bereinigt ({[b.get('tunes') for b in inner]})",
       inner[0].get("tunes") == {"hkstyle": {"c": "orange"}} and "tunes" not in inner[1])
    cols = got[14]["data"]["cols"]
    ok("G3: verschachtelt in Spalten bereinigt, Spalten-Block selbst ohne Farbe",
       cols[0]["blocks"][0].get("tunes") == {"hkstyle": {"c": "green", "bg": True}} and "tunes" not in cols[1]["blocks"][0] and "tunes" not in got[14])
    from saga.notes import blocks_text
    ok("G3: Suchtext enthaelt keine Farbnamen aus tunes", "orange" not in blocks_text(got) and "purple" not in blocks_text(got))

    # --- G10: Toggle-Ueberschrift ---------------------------------------------------------------------------------
    got = save(A, nid, [
        {"type": "toggle", "data": {"title": "Eins", "level": 1, "blocks": [P("x")]}},
        {"type": "toggle", "data": {"title": "Zu hoch", "level": 9}},
        {"type": "toggle", "data": {"title": "Null", "level": 0}},
        {"type": "toggle", "data": {"title": "Text", "level": "2"}},
        {"type": "toggle", "data": {"title": "Neg", "level": -1}},
        {"type": "columns", "data": {"cols": [{"blocks": [{"type": "toggle", "data": {"title": "In Spalte", "level": 7}}]}, {"blocks": []}]}},
    ])
    lv = [b["data"].get("level") for b in got[:5]]
    ok(f"G10: level 1-3 bleibt, >3 -> 3, 0/ungueltig/negativ -> entfernt ({lv})", lv == [1, 3, None, None, None])
    ok("G10: verschachtelt in Spalten begrenzt", got[5]["data"]["cols"][0]["blocks"][0]["data"].get("level") == 3)

    # --- Unbekannte Felder in data bleiben (Regel 0) ------------------------------------------------------------
    got = save(A, nid, [{"type": "image", "data": {"file": {"url": url}, "zukunft": {"a": 1}, "caption": "c"}},
                        {"type": "toggle", "data": {"title": "t", "open": False, "neu": 5, "blocks": []}},
                        {"type": "columns", "data": {"cols": [{"blocks": [], "extra": "e"}, {"blocks": []}], "gap": 2}}])
    ok("Regel 0: unbekannte Felder in data bleiben", got[0]["data"].get("zukunft") == {"a": 1} and got[1]["data"].get("neu") == 5
       and got[2]["data"]["cols"][0].get("extra") == "e" and got[2]["data"].get("gap") == 2)

    # --- G9: Schrift und kleiner Text ---------------------------------------------------------------------------
    s, _, n = req("GET", f"/api/notes/{nid}", A)
    ok(f"G9: GET liefert font=null, small=false als Standard ({n.get('font')!r}, {n.get('small')!r})", n.get("font") is None and n.get("small") is False)
    s, _, d = req("PATCH", f"/api/notes/{nid}", A, {"font": "serif", "small": True})
    n = req("GET", f"/api/notes/{nid}", A)[2]
    ok(f"G9: PATCH font=serif, small=true ({s})", s == 200 and n["font"] == "serif" and n["small"] is True)
    req("PATCH", f"/api/notes/{nid}", A, {"font": "mono"})
    ok("G9: mono", req("GET", f"/api/notes/{nid}", A)[2]["font"] == "mono")
    s, _, d = req("PATCH", f"/api/notes/{nid}", A, {"font": "Comic Sans"})
    ok(f"G9: unbekannte Schrift -> 400 ({s})", s == 400 and req("GET", f"/api/notes/{nid}", A)[2]["font"] == "mono")
    s, _, _ = req("PATCH", f"/api/notes/{nid}", A, {"font": "default", "small": False})
    n = req("GET", f"/api/notes/{nid}", A)[2]
    ok("G9: font=default -> null, small=false", s == 200 and n["font"] is None and n["small"] is False)
    req("PATCH", f"/api/notes/{nid}", A, {"font": "serif", "small": True, "wide": True})
    s, _, dup = req("POST", f"/api/notes/{nid}/duplicate", A, {})
    n2 = req("GET", f"/api/notes/{dup['id']}", A)[2] if s == 200 else {}
    ok(f"G9: Duplizieren uebernimmt Schrift und kleinen Text ({s})", n2.get("font") == "serif" and n2.get("small") is True and n2.get("wide") is True)
    s, _, d = req("PATCH", f"/api/notes/{nid}", A, {"font": None})
    ok("G9: font=null -> Standard", s == 200 and req("GET", f"/api/notes/{nid}", A)[2]["font"] is None)
    req("PATCH", f"/api/notes/{nid}", A, {"font": "serif"})

    # --- Veroeffentlichen -----------------------------------------------------------------------------------------
    auth.save_setting("publish_enabled", True)
    req("PUT", "/api/account/public-name", A, {"name": "gs-gestalt"})
    pub = req("POST", "/api/notes", A, {"title": "Gestaltet"})[2]["id"]
    save(A, pub, [
        P("Rot", tunes={"hkstyle": {"c": "red"}}),
        {"type": "header", "data": {"text": "Blau H", "level": 2}, "tunes": {"hkstyle": {"c": "blue", "bg": True}}},
        {"type": "image", "data": {"file": {"url": url}, "width": 42, "align": "right", "caption": "Bild"}},
        {"type": "image", "data": {"file": {"url": url}, "width": 42, "stretched": True}},
        {"type": "embed", "data": {"url": "https://www.youtube.com/watch?v=abcdefghijk", "kind": "youtube", "width": 70, "align": "left"}},
        {"type": "embed", "data": {"url": "https://example.org/k", "title": "Karte"}},
        {"type": "columns", "data": {"cols": [{"w": 0.7, "blocks": [P("L", tunes={"hkstyle": {"c": "green"}})]}, {"w": 0.3, "blocks": [P("R")]}]}},
        {"type": "toggle", "data": {"title": "Klapp H", "level": 2, "blocks": [P("innen")]}},
        {"type": "toggle", "data": {"title": "Normal", "blocks": [P("i")]}},
    ])
    # Feld per SQL verfaelschen (alte/fremde Daten): Veroeffentlichen darf trotzdem nur Palette/Zahlen ausgeben
    with connect() as c, c:
        b = json.loads(c.execute("SELECT body FROM note WHERE id=?", (pub,)).fetchone()[0])
        b["blocks"].append(P("boese", tunes={"hkstyle": {"c": "red\" onclick=\"x"}}))
        b["blocks"].append({"type": "image", "data": {"file": {"url": url}, "width": "100%\" onload=\"x", "align": "left\" x=\""}})
        b["blocks"].append({"type": "columns", "data": {"cols": [{"w": "1\" x", "blocks": [P("q")]}, {"w": 1, "blocks": [P("r")]}]}})
        c.execute("UPDATE note SET body=?, font='serif', small=1 WHERE id=?", (json.dumps(b), pub))
    s, _, d = req("PUT", f"/api/notes/{pub}/publish", A, {"slug": "gestaltet"})
    ok(f"Veroeffentlichen: an ({s})", s == 200)
    s, hd, html = req("GET", "/@gs-gestalt/gestaltet")
    ok(f"Veroeffentlichen: Seite erreichbar ({s})", s == 200 and "Gestaltet" in html)
    ok("Veroeffentlichen: CSP ohne Inline-Styles/Skripte", "style-src 'self'" in hd.get("content-security-policy", "") and "unsafe-inline" not in hd.get("content-security-policy", ""))
    ok("Veroeffentlichen: keine style-Attribute im Inhalt", " style=" not in html)
    ok("Veroeffentlichen: Textfarbe als Klasse c-red", '<p class="c-red">Rot</p>' in html)
    ok("Veroeffentlichen: Hintergrund als Klasse bg-blue an der Ueberschrift", '<h3 class="bg-blue">Blau H</h3>' in html)
    ok("Veroeffentlichen: Bild 42 % -> Klasse iw-40, rechts", re.search(r'<figure class="pub-img iw iw-40 al-right">', html) is not None)
    ok("Veroeffentlichen: volle Breite gewinnt gegen width", re.search(r'<figure class="pub-img wide">', html) is not None)
    ok("Veroeffentlichen: Player-Breite und Ausrichtung", 'class="pub-embed iw iw-70 al-left"' in html)
    ok("Veroeffentlichen: Link-Karte unveraendert", '<p class="pub-embed"><a href="https://example.org/k"' in html)
    ok("Veroeffentlichen: Spaltenbreiten als Klassen pw-70/pw-30",
       '<div class="pub-cols n2 fw"><div class="pub-col pw-70"><p class="c-green">L</p></div><div class="pub-col pw-30">' in html)
    ok("Veroeffentlichen: Toggle-Ueberschrift als details/summary/h3",
       '<details class="pub-toggle th" open><summary><h3>Klapp H</h3></summary>' in html)
    ok("Veroeffentlichen: normaler Toggle unveraendert", '<details class="pub-toggle" open><summary>Normal</summary>' in html)
    ok("Veroeffentlichen: verfaelschte Werte erscheinen nicht", "onclick" not in html and "onload" not in html and ' x="' not in html
       and '<p>boese</p>' in html)
    ok("Veroeffentlichen: Spalten mit kaputtem w -> gleich breit", '<div class="pub-cols n2"><div class="pub-col"><p>q</p>' in html)
    ok("Veroeffentlichen: Schrift und kleiner Text am body", re.search(r'<body class="pub[^"]* pub-f-serif pub-small"', html) is not None)
    s, _, css = req("GET", "/static/public.css")
    ok("public.css: Klassen fuer Farben (hell/dunkel), Breiten, Spalten",
       all(x in css for x in (".c-red{color:var(--tc-red)}", ".bg-blue{background:var(--bc-blue)}", ".iw-40{width:40%}", ".pw-70{flex-grow:70}",
                              "prefers-color-scheme: dark", ".pub-f-serif", ".pub-small")))

    # --- Markdown-Export ------------------------------------------------------------------------------------------
    s, hd, raw = req("GET", f"/api/notes/{pub}/export", A)
    md = raw if isinstance(raw, str) else raw.decode("utf-8", "replace") if isinstance(raw, bytes) else ""
    if isinstance(raw, bytes) and raw[:2] == b"PK":
        z = zipfile.ZipFile(io.BytesIO(raw)); md = "".join(z.read(n).decode() for n in z.namelist() if n.endswith(".md"))
    ok(f"Markdown: Toggle-Ueberschrift als '## Titel' mit Inhalt darunter ({s})", "## Klapp H\n\ninnen" in md)
    ok("Markdown: normaler Toggle bleibt <details>", "<details><summary>Normal</summary>" in md)
    ok("Markdown: Farbe entfaellt", "red" not in md and "hkstyle" not in md)

    req("DELETE", f"/api/notes/{pub}/publish", A)

    # --- Foederation: gleicher Handler; font/small gehen mit "db" durch, fremde Werte werden beim Lesen geglaettet ----
    from saga import federation as F
    ok("Foederation: PATCH nimmt font/small (EDIT2_KEYS)", "font" in F.EDIT2_KEYS and "small" in F.EDIT2_KEYS)
    t1 = F._typed({"font": "comic", "small": 1})
    t2 = F._typed({"font": "mono", "small": True})
    ok(f"Foederation: fremde Schrift -> Standard, small nur bool ({t1})", t1["font"] is None and t1["small"] is False and t2 == {**t2, "font": "mono", "small": True})
    ok(f"Keine Serverfehler (HTTP 5xx) in dieser Reihe ({n500[0]})", n500[0] == 0)


try:
    main()
except Exception as ex:  # noqa: BLE001
    import traceback; traceback.print_exc(); ok(f"Ausnahme: {ex}", False)
finally:
    cleanup()
print(f"\nGESTALTEN {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER"); sys.exit(0 if res and all(res) else 1)
