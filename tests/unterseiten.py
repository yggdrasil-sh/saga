"""Unterseiten (2.5, Widget-Art subpages): direkte Unterseiten einer Seite als Liste oder Galerie (saga/page_widgets.py).

Geprueft:
- Faehigkeit subpages1; Bereinigung (unbekannte cfg-Werte -> Standard, Unbekanntes faellt weg, size/style bleiben).
- Inhalt: nur aktive Unterseiten (geloescht, archiviert, Vorlagen fehlen), Reihenfolge wie im Seitenbaum (auch nach
  Verschieben), Umbenennen/Verschieben/Loeschen beim naechsten Abruf sichtbar (kein Zwischenspeicher), Datenbanken als kind.
- Kartenbild: cover (Titelbild, auch aus der Titelbild-Galerie), content (erstes Bild im Inhalt, auch in Spalten; frisch
  gespeichert = vor dem Abgleich von note_derived), none; fehlt das Bild -> img null.
- Rechte: geteilte Seite (Lesen) -> gleiche Unterseiten, can_add nur mit Bearbeiten; ohne Zugriff auf die Seite -> keine Werte;
  Vorschau (POST .../widgets/preview) ab Bearbeiten.
- Grenze SP_MAX (200) mit more/total.
- Veroeffentlichen: ohne "Unterseiten mitveroeffentlichen" nur eigens veroeffentlichte Unterseiten, private Titel/Bilder nie
  im HTML; mit Unterseiten alle aktiven; Bilder nur ueber die Adresse der Veroeffentlichung; keine Unterseite -> Block weg.
- Foederation (Einheit): clean_remote_data setzt IDs auf negative IDs und Bilder auf /api/fed/files/<unterseite>/<datei> um,
  Galerie-Titelbilder und Fremdes fallen weg; ohne Instanz -> None. Ueber echte Servergrenzen: tests/foederation_widgets2.py.
- Markdown-Export-Text.
Wegwerf-Konten sp-a/sp-b/sp-c, raeumt auf. Umgebung wie tests/widgets2.py (HK_TEST_BASE, BASE_URL, SESSION_SECRET, DATABASE_URL)."""
import base64, http.client, io, json, os, re, sys, time, urllib.parse
from itsdangerous import TimestampSigner
from PIL import Image
import auth
from db import connect

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from saga import page_widgets  # noqa: E402

B = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
res = []
n500 = [0]


def ok(n, c, info=None):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, "" if c or info is None else f"-> {str(info)[:500]}", flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, cookie=None, body=None, hdr=None, raw=None):
    c = http.client.HTTPConnection(B.hostname, B.port, timeout=60)
    h = {"Host": HOST, "Accept-Language": "de"}
    if cookie:
        h["Cookie"] = "saga=" + cookie; h["X-Saga"] = "1"
    if body is not None:
        h["Content-Type"] = "application/json"; raw = json.dumps(body)
    h.update(hdr or {})
    c.request(m, p, body=raw, headers=h)
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
    return r.status, data


def png(color, size=(300, 450)):
    b = io.BytesIO(); Image.new("RGB", size, color).save(b, "PNG"); return b.getvalue()


def upload(cookie, data, name="bild.png", note=None):
    bd = "sp-test"
    mp = (f"--{bd}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\nContent-Type: image/png\r\n\r\n").encode() \
        + data + f"\r\n--{bd}--\r\n".encode()
    s, d = req("POST", "/api/files" + (f"?note={note}" if note else ""), cookie, hdr={"Content-Type": f"multipart/form-data; boundary={bd}"}, raw=mp)
    assert s == 200, (s, d)
    return d["file"]["url"]


def new(cookie, title, parent=None, **kw):
    s, d = req("POST", "/api/notes", cookie, dict({"title": title, "parent_id": parent, "cover": None}, **kw))
    assert s == 200, (s, d)
    return d["id"]


def save(cookie, nid, blocks):
    s, d = req("PATCH", f"/api/notes/{nid}", cookie, {"body": json.dumps({"blocks": blocks}), "format": "blocks"})
    assert s == 200, (s, d)


def SP(cfg=None, bid="sp", **kw):
    return {"id": bid, "type": "widget", "data": dict({"v": 1, "kind": "subpages", "cfg": cfg or {}}, **kw)}


def wget(cookie, nid, bid="sp"):
    s, d = req("GET", f"/api/notes/{nid}/widgets", cookie)
    return s, ((d.get("widgets") or {}).get(bid) if isinstance(d, dict) else None) or {}


def ids(w):
    return [x["id"] for x in ((w.get("data") or {}).get("items") or [])]


def cleanup():
    with connect() as c, c:
        for t in ("note", "note_version", "user_kv", "file", "note_publish", "public_name"):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE 'sp-%'")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'sp-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'sp-%'")


def main():
    cleanup()
    for u in ("sp-a", "sp-b", "sp-c"):
        auth.create_user(u, "Unterseiten-Pass-123", u)
    A, Bk, C = ck("sp-a"), ck("sp-b"), ck("sp-c")
    s, d = req("GET", "/api/version")
    ok("Faehigkeit subpages1", "subpages1" in d.get("features", []), d.get("features"))
    part_clean()
    root, kids = part_content(A)
    part_images(A, root, kids)
    part_rights(A, Bk, C, root, kids)
    part_limit(A)
    part_publish(A, root, kids)
    part_fed()
    part_md(A, root)


def part_clean():
    d = page_widgets.clean_data({"kind": "subpages", "size": "l", "style": "card", "accent": "red", "junk": 1,
                                 "cfg": {"view": "kacheln", "image": "foto", "page": 5, "x": "<b>"}})
    ok("Bereinigung: unbekannte Ansicht/Bildquelle -> list/cover, Fremdes in cfg faellt weg, size/style bleiben",
       d == {"v": 1, "kind": "subpages", "size": "l", "style": "card", "accent": "red", "cfg": {"view": "list", "image": "cover"}}, d)
    d = page_widgets.clean_data({"kind": "subpages", "cfg": {"view": "gallery", "image": "content"}})
    ok("Bereinigung: gallery/content bleibt", d["cfg"] == {"view": "gallery", "image": "content"}, d)
    d = page_widgets.clean_data({"kind": "subpages", "cfg": "kaputt"})
    ok("Bereinigung: cfg kein Objekt -> Standard", d["cfg"] == {"view": "list", "image": "cover"}, d)
    ok("subpages ist eine vom Server gerechnete Art (KINDS2) und nie persoenlich", "subpages" in page_widgets.KINDS2
       and page_widgets.server_kind(d) and "subpages" not in page_widgets.PERSONAL)


def part_content(A):
    root = new(A, "Sammlung")
    kids = [new(A, f"Kind {i}", root) for i in range(5)]
    dbk = new(A, "Tabelle", root, kind="database")
    tpl = new(A, "Vorlage", root)
    req("PATCH", f"/api/notes/{tpl}", A, {"template": True})
    arch = new(A, "Archiviert", root)
    req("POST", f"/api/notes/{arch}/archive", A, {"archived": True})
    gone = new(A, "Geloescht", root)
    req("DELETE", f"/api/notes/{gone}", A)
    enkel = new(A, "Enkel", kids[0])
    save(A, root, [{"type": "paragraph", "data": {"text": "Oben"}}, SP({"view": "list"})])
    s, w = wget(A, root)
    ok(f"Liste: nur aktive direkte Unterseiten in Baum-Reihenfolge (ohne Vorlage, Archiv, Papierkorb, Enkel) ({s})",
       s == 200 and w.get("kind") == "subpages" and w.get("error") is None and ids(w) == kids + [dbk] and enkel not in ids(w)
       and w["value"] == 6 and w["data"]["total"] == 6 and w["data"]["more"] == 0, w)
    its = w.get("data", {}).get("items") or [{}]
    ok("Eintrag: id, title, icon, img, kind; Datenbank als kind database", set(its[0]) == {"id", "title", "icon", "img", "kind"}
       and its[0]["title"] == "Kind 0" and its[-1]["kind"] == "database" and its[0]["kind"] == "page", its[:2])
    ok("Eigentuemer darf anlegen (can_add)", w["data"]["can_add"] is True)
    req("POST", f"/api/notes/{kids[3]}/move", A, {"parent_id": root, "index": 0})
    req("PATCH", f"/api/notes/{kids[1]}", A, {"title": "Kind Eins neu", "icon": "🦊"})
    req("DELETE", f"/api/notes/{kids[4]}", A)
    neu = new(A, "Ganz neu", root)
    s, w = wget(A, root)
    t = {x["id"]: x for x in w["data"]["items"]}
    ok("Live: verschoben, umbenannt (mit Symbol), geloescht, neu - sofort beim naechsten Abruf",
       ids(w) == [kids[3], kids[0], kids[1], kids[2], dbk, neu] and t[kids[1]]["title"] == "Kind Eins neu" and t[kids[1]]["icon"] == "🦊", ids(w))
    req("POST", f"/api/notes/{kids[2]}/move", A, {"parent_id": kids[0], "index": 0})
    s, w = wget(A, root)
    ok("Live: in eine andere Seite verschoben -> weg", kids[2] not in ids(w), ids(w))
    req("POST", f"/api/notes/{kids[2]}/move", A, {"parent_id": root, "index": 3})
    req("POST", f"/api/trash/{kids[4]}/restore", A)
    kids = [x for x in ids(wget(A, root)[1]) if x not in (dbk, neu)]
    s, w = wget(A, new(A, "Leer"))
    ok("Seite ohne den Block: keine Werte", s == 200 and w == {}, w)
    leer = new(A, "Leer2")
    save(A, leer, [SP()])
    s, w = wget(A, leer)
    ok("Seite ohne Unterseiten: leere Liste, total 0", w.get("data") == {"items": [], "total": 0, "more": 0, "can_add": True}, w)
    return root, kids


def part_images(A, root, kids):
    k0, k1, k2 = kids[0], kids[1], kids[2]
    f_cover = upload(A, png("#336699", (900, 300)), "titel.png")
    f_body = upload(A, png("#993333"), "cover.png")
    f_col = upload(A, png("#339933"), "spalte.png")
    req("PATCH", f"/api/notes/{k0}", A, {"cover": f_cover})
    req("PATCH", f"/api/notes/{k1}", A, {"cover": "/covers/x-gibt-es-nicht.jpg"})       # unbekannt: Server lehnt ab
    s, cv = req("GET", "/api/covers", A)
    gal_cover = None
    if s == 200 and isinstance(cv, dict):
        gal_cover = next((x.get("url") for x in cv.get("covers") or []), None)
    if gal_cover:
        req("PATCH", f"/api/notes/{k1}", A, {"cover": gal_cover})
    save(A, k0, [{"type": "image", "data": {"file": {"url": f_body}, "caption": ""}}])
    save(A, k2, [{"type": "paragraph", "data": {"text": "Text vorn"}},
                 {"type": "columns", "data": {"cols": [{"blocks": [{"type": "paragraph", "data": {"text": "links"}}]},
                                                       {"blocks": [{"type": "image", "data": {"file": {"url": f_col}, "caption": ""}}]}]}}])
    save(A, root, [SP({"view": "gallery", "image": "cover"})])
    s, w = wget(A, root)
    im = {x["id"]: x["img"] for x in w["data"]["items"]}
    ok("Kartenbild cover: Titelbild (eigene Datei bzw. Galerie), sonst null", im.get(k0) == f_cover and im.get(k2) is None
       and (im.get(k1) == gal_cover if gal_cover else im.get(k1) is None), (im, gal_cover))
    save(A, root, [SP({"view": "gallery", "image": "content"})])
    s, w = wget(A, root)
    im = {x["id"]: x["img"] for x in w["data"]["items"]}
    ok("Kartenbild content: erstes Bild im Inhalt (auch in Spalten), frisch gespeichert", im.get(k0) == f_body and im.get(k2) == f_col
       and im.get(k1) is None, im)
    from saga import pageindex
    for _ in range(20):
        if not pageindex.pending(["sp-a"]):
            break
        time.sleep(0.5)
    s, w = wget(A, root)
    im2 = {x["id"]: x["img"] for x in w["data"]["items"]}
    ok("Kartenbild content: nach dem Abgleich (note_derived) gleich", im2 == im, (im2, im))
    save(A, root, [SP({"view": "gallery", "image": "none"})])
    s, w = wget(A, root)
    ok("Kartenbild none: keine Bilder", all(x["img"] is None for x in w["data"]["items"]), w)
    s, d = req("POST", f"/api/notes/{root}/widgets/preview", A, {"block": SP({"view": "gallery", "image": "content"}, bid="pv")})
    ok(f"Vorschau beim Einrichten rechnet mit derselben Seite ({s})", s == 200 and d["widget"]["data"]["items"][0]["img"] == f_body, d)
    save(A, root, [SP({"view": "gallery", "image": "content"})])


def part_rights(A, Bk, C, root, kids):
    req("PUT", f"/api/notes/{root}/shares", A, {"type": "user", "target": "sp-b", "role": "view"})
    s, wa = wget(A, root)
    s, wb = wget(Bk, root)
    ok(f"Geteilt (Lesen): gleiche Unterseiten und Bilder, can_add false ({s})", s == 200 and ids(wb) == ids(wa)
       and [x["img"] for x in wb["data"]["items"]] == [x["img"] for x in wa["data"]["items"]] and wb["data"]["can_add"] is False, wb)
    img = next((x["img"] for x in wb["data"]["items"] if x["img"]), None)
    s, _ = req("GET", img + "?w=400", Bk) if img else (0, None)
    ok(f"Geteilt: Kartenbild fuer die betrachtende Person abrufbar ({s})", s == 200)
    s, _ = req("GET", img + "?w=400", C) if img else (0, None)
    ok(f"Ohne Freigabe: Kartenbild nicht abrufbar ({s})", s == 404)
    s, d = req("GET", f"/api/notes/{root}/widgets", C)
    ok(f"Ohne Zugriff auf die Seite: keine Werte ({s})", s in (403, 404) and "widgets" not in (d if isinstance(d, dict) else {}), d)
    req("PUT", f"/api/notes/{kids[0]}/shares", A, {"type": "user", "target": "sp-c", "role": "view"})
    s, d = req("GET", f"/api/notes/{root}/widgets", C)
    ok(f"Nur eine Unterseite geteilt: Elternseite bleibt zu, keine Titel ({s})", s in (403, 404) and "Kind" not in json.dumps(d), d)
    s, d = req("POST", f"/api/notes/{root}/widgets/preview", Bk, {"block": SP(bid="pv")})
    ok(f"Vorschau mit Leserecht abgelehnt ({s})", s in (403, 404), d)
    req("PUT", f"/api/notes/{root}/shares", A, {"type": "user", "target": "sp-b", "role": "edit"})
    s, wb = wget(Bk, root)
    ok("Geteilt (Bearbeiten): can_add true", wb.get("data", {}).get("can_add") is True, wb)
    own = new(Bk, "Von Bert", root)
    s, wb = wget(Bk, root)
    s2, wa = wget(A, root)
    ok("Bert legt eine Unterseite an: gehoert Anna, beide sehen sie", own in ids(wb) and own in ids(wa))
    # Unterseite einer Seite, die selbst unter einer geteilten liegt - Kette ueber zwei Ebenen
    sub = new(A, "Unterbereich", kids[0])
    save(A, kids[0], [SP(bid="sp2")])
    s, wb = wget(Bk, kids[0], "sp2")
    ok(f"Geerbte Freigabe zwei Ebenen tief ({s})", ids(wb) == [sub], wb)
    req("DELETE", f"/api/notes/{root}/shares", A, {"type": "user", "target": "sp-b"})


def part_limit(A):
    big = new(A, "Gross")
    with connect() as c, c:
        now = time.time()
        c.executemany("INSERT INTO note (uid, title, body, format, parent_id, sort, created, updated) VALUES (?,?,?,?,?,?,?,?)",
                      [("sp-a", f"Seite {i:03d}", '{"blocks": []}', "blocks", big, i + 1, now, now) for i in range(page_widgets.SP_MAX + 15)])
    save(A, big, [SP()])
    t0 = time.time()
    s, w = wget(A, big)
    dt = time.time() - t0
    its = w.get("data", {}).get("items") or []
    ok(f"Grenze: {page_widgets.SP_MAX} Eintraege, more 15, total {page_widgets.SP_MAX + 15} ({dt:.2f} s)", len(its) == page_widgets.SP_MAX
       and w["data"]["more"] == 15 and w["data"]["total"] == page_widgets.SP_MAX + 15 and its[0]["title"] == "Seite 000"
       and its[-1]["title"] == f"Seite {page_widgets.SP_MAX - 1:03d}", (len(its), w.get("data", {}).get("more")))
    ok("Grenze: Abruf zuegig (< 2 s)", dt < 2, dt)


def part_publish(A, root, kids):
    save(A, root, [{"type": "paragraph", "data": {"text": "Sammlung oeffentlich"}},
                   SP({"view": "gallery", "image": "content"}, title="Meine Unterseiten")])
    req("PATCH", f"/api/notes/{kids[1]}", A, {"title": "Geheimer Titel"})
    pub_kid = kids[2]                            # hat ein Bild in Spalten (part_images)
    s, d = req("PUT", f"/api/notes/{pub_kid}/publish", A, {"slug": "sp-kind", "name": "sp-anna"})
    s, d = req("PUT", f"/api/notes/{root}/publish", A, {"slug": "sp-sammlung", "name": "sp-anna"})
    url = urllib.parse.urlparse(str((d or {}).get("url") or "")).path if s == 200 else ""
    s, html = req("GET", url) if url else (s, "")
    html = str(html)
    body = html.split("<body", 1)[-1]
    ok(f"Veroeffentlicht ohne Unterseiten: nur die eigens veroeffentlichte Unterseite ({s})", s == 200 and "pub-sp-gallery" in html
       and "Kind Eins neu" in body and "Kind 3" not in body and "Geheimer Titel" not in body and "Von Bert" not in body and body.count('class="pub-sp-it"') == 1, body[:3000])
    ok("Veroeffentlicht: Link auf die oeffentliche Adresse, Bild ueber die Veroeffentlichung (kein /files/)", "/@sp-anna/sp-kind" in body
       and "/files/" not in body and re.search(r'<img src="[^"]*/f/[0-9a-f]{24}\?w=640"', body), body[body.find("pub-sp-items"):][:800])
    ok("Veroeffentlicht: keine Inline-Styles, kein Skript", " style=" not in body and "<script" not in html.split("<main", 1)[-1])
    req("PUT", f"/api/notes/{root}/publish", A, {"children": True})
    s, html = req("GET", url)
    body = str(html).split("<body", 1)[-1]
    ok(f"Mit Unterseiten: alle aktiven Unterseiten (auch umbenannte) ({s})", "Geheimer Titel" in body and "Archiviert" not in body
       and "Vorlage" not in body and "Geloescht" not in body and body.count('class="pub-sp-it"') >= 4, body[:2000])
    req("DELETE", f"/api/notes/{root}/publish", A)
    req("DELETE", f"/api/notes/{pub_kid}/publish", A)
    solo = new(A, "Allein")
    new(A, "Privates Kind", solo)
    save(A, solo, [{"type": "paragraph", "data": {"text": "Nur Text"}}, SP(title="Kinderliste")])
    s, d = req("PUT", f"/api/notes/{solo}/publish", A, {"slug": "sp-allein", "name": "sp-anna"})
    url2 = urllib.parse.urlparse(str((d or {}).get("url") or "")).path if s == 200 else ""
    s, html = req("GET", url2) if url2 else (s, "")
    body = str(html).split("<body", 1)[-1]
    ok(f"Keine Unterseite oeffentlich: Block entfaellt, kein Titel ({s})", s == 200 and "Nur Text" in body and "Kinderliste" not in body
       and "Privates Kind" not in body and "pub-sp" not in body, body[:1500])
    req("DELETE", f"/api/notes/{solo}/publish", A)


def part_fed():
    from saga import federation as F, fed_pages
    data = {"items": [{"id": 7, "title": "  Fremd  <b>x</b> ", "icon": "/files/" + "a" * 24, "img": "/files/" + "b" * 24, "kind": "page"},
                      {"id": 8, "title": "Galerie", "icon": "🌲", "img": "/covers/nordisch-1.jpg", "kind": "database"},
                      {"id": 9, "title": "Boese", "icon": "<img>", "img": "https://boese.example/x.png", "kind": "x"},
                      {"id": -3, "title": "Ungueltig"}, "kaputt"],
            "total": 5, "more": 0, "can_add": True, "extra": "weg"}
    out = page_widgets.clean_remote_data("subpages", data, 3)
    r7, r8, r9 = F.ref(3, 7), F.ref(3, 8), F.ref(3, 9)
    ok("Foederation: IDs -> negative IDs der Instanz, ungueltige Eintraege fallen weg", out and [x["id"] for x in out["items"]] == [r7, r8, r9], out)
    it = {x["id"]: x for x in (out or {}).get("items", [])}
    ok("Foederation: eigene Bilder ueber /api/fed/files/<unterseite>/<datei>, Galerie/fremde Adressen weg",
       it.get(r7, {}).get("img") == f"/api/fed/files/{r7}/{'b' * 24}" and it[r7]["icon"] == f"/api/fed/files/{r7}/{'a' * 24}"
       and it[r8]["img"] is None and it[r8]["icon"] == "🌲" and it[r9]["img"] is None and it[r9]["icon"] == "" and it[r9]["kind"] == "page", it)
    ok("Foederation: nur bekannte Felder, total/more stimmig", set(out) == {"items", "total", "more", "can_add"} and out["total"] == 5
       and out["more"] == 2, out)
    ok("Foederation: ohne Instanz kein Ergebnis", page_widgets.clean_remote_data("subpages", data, None) is None)
    x = fed_pages._w_widget_in({"kind": "subpages", "value": 3, "data": data}, 3)
    ok("Foederation: _w_widget_in uebernimmt data umgesetzt", x["data"]["items"][0]["id"] == r7 and x["value"] == 3, x)


def part_md(A, root):
    s, w = wget(A, root)
    txt = page_widgets.value_text({"kind": "subpages"}, w, None)
    ok("Markdown-Text: Titel mit Trenner", txt and txt.startswith("Kind 3 · ") and "Von Bert" in txt, txt)
    ok("Markdown-Text: leer", page_widgets.value_text({"kind": "subpages"}, {"data": {"items": []}}, None) == "Keine Unterseiten")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        res.append(False)
    finally:
        cleanup()
    ok(f"Serverfehler: {n500[0]}", n500[0] == 0)
    print(f"\nUNTERSEITEN {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)
