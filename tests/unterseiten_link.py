"""Verweis auf neue Unterseiten am Ende der Elternseite (2.5, Bug 2151, saga/sublink.py) gegen einen laufenden Server.

Geprueft (je mit Gegenprobe):
- POST /api/notes mit parent_id: Absatz <a href="/notes#ID">Titel</a> am Ende (vor einem leeren Schluss-Absatz), rev+1,
  bisheriger Inhalt unveraendert; "link": false -> nichts; ohne parent_id -> nichts.
- Nicht verlinkt: Elternseite Datenbank (Zeile), gesperrt (423-frei: Seite entsteht, Inhalt bleibt), Text-Notiz,
  archiviert; neue Vorlage (template); schon vorhandener Verweis (Verschieben zurueck) -> kein zweiter.
- Geteilte Seite mit "Bearbeiten": Verweis kommt in die Seite des Eigentuemers; "Lesen" -> 403, Inhalt bleibt.
- Erster Titel: unbenannt angelegt -> Titel (auch in Etappen, mit Symbol) zieht den automatischen Absatz nach;
  Gegenproben: mit Titel angelegt, von Hand geaendert, nach 10 Minuten, gesperrte Elternseite, spaetere Umbenennung.
- Aeltere Apps ("/Seite", "/Datenbank": legen an und setzen den Verweis bzw. Datenbank-Block selbst): automatischer Absatz
  faellt beim Speichern weg (zusammengefuehrt und mit aktueller rev, auch wenn ein veralteter Client ihn wieder mitschickt);
  Gegenproben: normales Speichern, spaeterer Verweis im Text.
- Duplizieren: Kopie direkt hinter dem Verweis auf das Original; ohne Verweis auf das Original -> ans Ende;
  aus einer eigenen Vorlage -> ans Ende (nicht hinter die Vorlage); Duplikat als Vorlage -> nichts.
- Galerie (wiki) unter einer Seite: genau EIN Verweis (auf die Startseite), Innenleben der Vorlage unveraendert
  (gleicher Inhalt wie auf oberster Ebene angelegt).
- Unterseiten-Block (Widget subpages) in der Elternseite (auch in Spalten): kein Verweis - Anlegen, Duplizieren, Galerie,
  Verschieben hinein (aus der alten Seite faellt der reine Verweis-Absatz trotzdem weg); Gegenprobe ohne Block.
- Verschieben (POST /move und PATCH parent_id): neuer Verweis in der neuen Elternseite, reiner Verweis-Absatz in der alten
  entfernt, Verweis im Fliesstext bleibt; nur umsortieren -> nichts; nach oben -> nur entfernt; gesperrte alte Elternseite
  bleibt unveraendert.
- Konfliktweg: Client mit alter rev und merge: 1 -> eigene Aenderung UND Verweis bleiben; ohne merge -> 409.
- API v1 (POST /api/v1/pages) und MCP create_page mit parent_id -> Verweis; API v1 "link": false -> keiner.
- Import (Markdown-ZIP) mit parent_id: oberste importierte Seite verlinkt, innere nicht doppelt.
- Live (notes.live an): offener Live-Raum der Elternseite bekommt den Verweis als Update; ungespeicherte Live-Eingabe bleibt
  erhalten; nach dem Materialisieren stehen beide in note.body.
Wegwerf-Konten ul-a/ul-b; raeumt auf. Umgebung wie tests/collab.py (HK_TEST_BASE, SESSION_SECRET, BASE_URL, DATABASE_URL);
Server mit kurzen Fristen (COLLAB_MAT_DELAY=0.5 COLLAB_COMPACT_IDLE=3) fuer den Live-Teil."""
import base64, http.client, io, json, os, re, sys, time, urllib.parse, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
from itsdangerous import TimestampSigner  # noqa: E402
import auth  # noqa: E402
from db import connect  # noqa: E402

B = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
WS = f"ws://{B.hostname}:{B.port}/api/collab"
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
UA, UB = "ul-a", "ul-b"
res, n500 = [], [0]


def ok(n, c, info=None):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, "" if c or info is None else f"-> {str(info)[:600]}", flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5, "sec_at": int(time.time())}).encode())).decode()


def req(m, p, body=None, u=UA, bearer=None, raw=None, hdr=None):
    c = http.client.HTTPConnection(B.hostname, B.port, timeout=60)
    h = {"Host": HOST, "Accept-Language": "de"}
    if bearer:
        h["Authorization"] = "Bearer " + bearer
    elif u:
        h["Cookie"] = "saga=" + ck(u); h["X-Saga"] = "1"
    data = None
    if raw is not None:
        data, h["Content-Type"] = raw, "application/octet-stream"
    elif body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    h.update(hdr or {})
    c.request(m, p, body=data, headers=h)
    r = c.getresponse(); d = r.read(); c.close()
    if r.status >= 500:
        n500[0] += 1
    try:
        d = json.loads(d) if d[:1] in (b"{", b"[") else d
    except ValueError:
        pass
    return r.status, d


def P(text):
    return {"type": "paragraph", "data": {"text": text}}


def page(title, blocks=None, parent=None, u=UA, **kw):
    b = {"title": title, "cover": None, "link": False}
    if parent is not None:
        b["parent_id"] = parent
    b.update(kw)
    st, d = req("POST", "/api/notes", b, u)
    assert st == 200, d
    if blocks is not None:
        st2, d2 = req("PATCH", f"/api/notes/{d['id']}", {"body": json.dumps({"blocks": blocks}), "format": "blocks"}, u)
        assert st2 == 200, d2
    return d["id"]


def get(nid, u=UA):
    st, d = req("GET", f"/api/notes/{nid}", None, u)
    return d if st == 200 else {}


def blocks(nid, u=UA):
    d = get(nid, u)
    return (json.loads(d["body"]).get("blocks") or []) if d.get("format") == "blocks" else []


def links_to(bl, child):
    pat = '/notes#%d\\"' % child                # im JSON maskiertes Anfuehrungszeichen
    return [i for i, b in enumerate(bl) if pat in json.dumps(b.get("data"), ensure_ascii=False)]


def is_link_para(b, child):
    return b.get("type") == "paragraph" and re.fullmatch(r'<a href="/notes#%d">[^<]*</a>' % child, (b.get("data") or {}).get("text") or "")


def new_sub(parent, title="", u=UA, **kw):
    b = {"parent_id": parent, "title": title}
    b.update(kw)
    return req("POST", "/api/notes", b, u)


def live(on):
    auth.save_setting("notes", {"live": bool(on)})
    time.sleep(5.2)                      # Einstellungen sind 5 s zwischengespeichert


def zipped(files):
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)
    return bio.getvalue()


def wait_run(rid):
    t0, d = time.time(), {}
    while time.time() - t0 < 120:
        st, d = req("GET", f"/api/import/runs/{rid}")
        if st == 200 and d["state"] in ("done", "failed", "cancelled"):
            return d
        time.sleep(0.3)
    return d


def setup():
    from db import wait_ready, init_schema
    wait_ready(); init_schema()
    for u in (UA, UB):
        auth.get_user(u) or auth.create_user(u, None, u)


def cleanup():
    from saga import userdata
    for u in (UA, UB):
        try:
            if auth.get_user(u):
                userdata.delete_user_data(u, drop_user=True)
        except Exception as ex:  # noqa: BLE001
            print("Aufraeumen", u, ex)


def t_create():
    print("--- Anlegen ---")
    par = page("Projekte", [P("Einleitung"), P("")])
    rev0 = get(par)["rev"]
    st, d = new_sub(par, "Neues Projekt")
    ok("Unterseite angelegt", st == 200 and d.get("id"), d)
    kid = d["id"]
    bl = blocks(par)
    ok("Elternseite: Verweis-Absatz vor dem leeren Schluss-Absatz, Einleitung unveraendert",
       len(bl) == 3 and bl[0]["data"]["text"] == "Einleitung" and is_link_para(bl[1], kid) and bl[2]["data"]["text"] == ""
       and "Neues Projekt" in bl[1]["data"]["text"], bl)
    ok("rev der Elternseite +1", get(par)["rev"] == rev0 + 1, (rev0, get(par)["rev"]))
    ok("Block-ID vergeben", isinstance(bl[1].get("id"), str) and len(bl[1]["id"]) >= 6)
    st, d = new_sub(par, "Zweite")
    bl2 = blocks(par)
    ok("Zweite Unterseite: Verweis dahinter, erster bleibt", len(bl2) == 4 and is_link_para(bl2[1], kid) and is_link_para(bl2[2], d["id"])
       and bl2[3]["data"]["text"] == "", bl2)
    # Gegenprobe: link false, ohne parent
    st, d = new_sub(par, "Ohne Verweis", link=False)
    ok("link: false -> Seite entsteht, kein Verweis", st == 200 and not links_to(blocks(par), d["id"]) and len(blocks(par)) == 4)
    st, d = req("POST", "/api/notes", {"title": "Oben"})
    ok("ohne parent_id -> keine Elternseite beruehrt", st == 200 and len(blocks(par)) == 4)
    # leere Elternseite (neu, ohne Inhalt): Verweis als erster Block
    empty = page("Leer")
    st, d = new_sub(empty, "")
    bl = blocks(empty)
    ok("Leere Elternseite: genau ein Verweis-Absatz, Titel 'Unbenannt'", len(bl) == 1 and is_link_para(bl[0], d["id"]) and "Unbenannt" in bl[0]["data"]["text"], bl)
    # Symbol wird mitgeschrieben (Text-Symbol), Bild-Symbol nicht
    st, d = new_sub(empty, "Mit Symbol", icon="🚀")
    ok("Text-Symbol vor dem Titel", blocks(empty)[-1]["data"]["text"] == '<a href="/notes#%d">🚀 Mit Symbol</a>' % d["id"], blocks(empty)[-1])
    # HTML im Titel wird maskiert
    st, d = new_sub(empty, "<b>fett</b> & Co")
    ok("Titel maskiert (kein HTML aus dem Titel)", "<b>" not in blocks(empty)[-1]["data"]["text"] and "&amp;" in blocks(empty)[-1]["data"]["text"],
       blocks(empty)[-1])
    return par


def t_not():
    print("--- Nicht verlinken ---")
    dbid = page("DB", kind="database")
    before = get(dbid)
    st, d = new_sub(dbid, "Zeile")
    ok("Datenbank: Zeile entsteht, Datenbank-Inhalt/rev unveraendert", st == 200 and get(dbid)["rev"] == before["rev"]
       and get(dbid).get("body") == before.get("body"))
    lk = page("Gesperrt", [P("fest")])
    st, _ = req("PATCH", f"/api/notes/{lk}", {"locked": True})
    rv = get(lk)["rev"]
    st, d = new_sub(lk, "Unter gesperrt")
    ok("Gesperrte Elternseite: Unterseite entsteht, Inhalt und rev unveraendert",
       st == 200 and [b["data"]["text"] for b in blocks(lk)] == ["fest"] and get(lk)["rev"] == rv, blocks(lk))
    req("PATCH", f"/api/notes/{lk}", {"locked": False})
    st, d = new_sub(lk, "Nach Entsperren")
    ok("Gegenprobe: entsperrt -> Verweis", links_to(blocks(lk), d["id"]) == [1], blocks(lk))
    tx = page("Text")
    req("PATCH", f"/api/notes/{tx}", {"body": "nur Text", "format": "text"})
    st, d = new_sub(tx, "Unter Text")
    ok("Text-Notiz: unveraendert", st == 200 and get(tx).get("body") == "nur Text" and get(tx).get("format") == "text")
    ar = page("Archiv", [P("alt")])
    req("POST", f"/api/notes/{ar}/archive", {"archived": True})
    st, d = new_sub(ar, "Unter Archiv")
    ok("Archivierte Elternseite: kein Verweis", (st != 200) or not links_to(blocks(ar), d["id"]), (st, blocks(ar)))
    tp = page("Vorlagen-Elternseite", [P("x")])
    st, d = new_sub(tp, "Vorlage")
    vid = d["id"]
    ok("(Ausgangslage) normale Unterseite verlinkt", links_to(blocks(tp), vid) == [1])
    st, d = req("POST", f"/api/notes/{vid}/duplicate", {"parent_id": tp, "template": True})
    ok("Duplikat als Vorlage: kein Verweis", st == 200 and not links_to(blocks(tp), d["id"]), d)
    # Geteilt: b mit Bearbeiten
    sh = page("Geteilt", [P("Team")])
    st, d = req("PUT", f"/api/notes/{sh}/shares", {"type": "user", "target": UB, "role": "edit"})
    ok("(Ausgangslage) Freigabe Bearbeiten", st == 200, d)
    st, d = new_sub(sh, "Von B", u=UB)
    ok("Bearbeiten: Unterseite von B verlinkt in der Seite von A", st == 200 and links_to(blocks(sh), d["id"]) == [1], (st, d, blocks(sh)))
    ro = page("Nur lesen", [P("lies")])
    req("PUT", f"/api/notes/{ro}/shares", {"type": "user", "target": UB, "role": "view"})
    st, d = new_sub(ro, "Von B", u=UB)
    ok("Lesen: 403, Inhalt unveraendert", st == 403 and [b["data"]["text"] for b in blocks(ro)] == ["lies"], (st, blocks(ro)))


def W(bid="sp1"):
    return {"id": bid, "type": "widget", "data": {"v": 1, "kind": "subpages", "cfg": {}}}


def t_widget():
    print("--- Unterseiten-Block (Entscheidung 18.11.) ---")
    par = page("Mit Block", [P("oben"), W()])
    rv = get(par)["rev"]
    st, d = new_sub(par, "Im Block sichtbar")
    ok("Elternseite mit Unterseiten-Block: kein Verweis, rev unveraendert", st == 200 and not links_to(blocks(par), d["id"]) and get(par)["rev"] == rv,
       blocks(par))
    st, d2 = req("POST", f"/api/notes/{d['id']}/duplicate", {"parent_id": par})
    st3, d3 = req("POST", "/api/gallery/wiki/use?lang=de", {"parent_id": par, "examples": False})
    ok("Duplizieren und Galerie unter Seite mit Block: kein Verweis", get(par)["rev"] == rv and len(blocks(par)) == 2, blocks(par))
    # Block in Spalten: zaehlt auch
    nested = page("Block in Spalte", [{"id": "c1", "type": "columns", "data": {"cols": [{"w": 0.5, "blocks": [W("sp2")]}, {"w": 0.5, "blocks": [P("x")]}]}}])
    rv2 = get(nested)["rev"]
    new_sub(nested, "Spaltenkind")
    ok("Unterseiten-Block in einer Spalte zaehlt auch", get(nested)["rev"] == rv2)
    # Verschieben hinein: kein neuer Verweis; aus der alten Seite der reine Verweis-Absatz weg
    alt = page("Alt ohne Block", [P("a")])
    st, d = new_sub(alt, "Wandert in Block")
    w = d["id"]
    ok("(Ausgangslage) Verweis in alter Seite", links_to(blocks(alt), w) == [1])
    req("POST", f"/api/notes/{w}/move", {"parent_id": par, "index": 0})
    ok("Verschieben in Seite mit Block: dort kein Verweis, alter Verweis-Absatz entfernt",
       not links_to(blocks(par), w) and get(par)["rev"] == rv and not links_to(blocks(alt), w), (blocks(par), blocks(alt)))
    # Gegenprobe: Block entfernt -> wieder Verweis
    req("PATCH", f"/api/notes/{par}", {"body": json.dumps({"blocks": [P("oben")]}), "format": "blocks"})
    st, d = new_sub(par, "Nach dem Block")
    ok("Gegenprobe: ohne Unterseiten-Block wieder Verweis", links_to(blocks(par), d["id"]) == [1], blocks(par))


def t_retitle():
    print("--- Erster Titel zieht den Verweistext nach (Entscheidung 18.11.) ---")
    par = page("Titel-Eltern", [P("oben")])
    st, d = new_sub(par, "")
    kid = d["id"]
    bl = blocks(par)
    ok("Unbenannt angelegt: automatischer Absatz mit ID subu-...", is_link_para(bl[1], kid) and bl[1]["id"].startswith("subu-")
       and "Unbenannt" in bl[1]["data"]["text"], bl)
    rv = get(par)["rev"]
    req("PATCH", f"/api/notes/{kid}", {"title": "Neu"})
    req("PATCH", f"/api/notes/{kid}", {"title": "Neues Projekt"})        # Web speichert beim Tippen in Etappen
    bl = blocks(par)
    ok("Erster Titel (in Etappen): Verweistext = Titel, ID bleibt", bl[1]["data"]["text"] == '<a href="/notes#%d">Neues Projekt</a>' % kid
       and bl[1]["id"].startswith("subu-") and get(par)["rev"] == rv + 2, (bl, get(par)["rev"], rv))
    req("PATCH", f"/api/notes/{kid}", {"title": "Neues Projekt"})
    ok("Gleicher Titel noch einmal: nichts", get(par)["rev"] == rv + 2)
    # Symbol vor dem ersten Titel gesetzt
    st, d = new_sub(par, "")
    k2 = d["id"]
    req("PATCH", f"/api/notes/{k2}", {"icon": "🚀"})
    req("PATCH", f"/api/notes/{k2}", {"title": "Rakete"})
    ok("Mit Symbol: 'Symbol Titel'", blocks(par)[links_to(blocks(par), k2)[0]]["data"]["text"] == '<a href="/notes#%d">🚀 Rakete</a>' % k2,
       blocks(par))
    # Gegenproben
    st, d = new_sub(par, "Fest")
    k3 = d["id"]
    ok("(Ausgangslage) mit Titel angelegt: ID sub-...", blocks(par)[links_to(blocks(par), k3)[0]]["id"].startswith("sub-"))
    rv = get(par)["rev"]
    req("PATCH", f"/api/notes/{k3}", {"title": "Umbenannt"})
    ok("Mit Titel angelegt, dann umbenannt: Verweistext bleibt", get(par)["rev"] == rv and "Fest" in blocks(par)[links_to(blocks(par), k3)[0]]["data"]["text"])
    st, d = new_sub(par, "")
    k4 = d["id"]
    bl = blocks(par)
    i = links_to(bl, k4)[0]
    bl[i]["data"]["text"] = 'Siehe <a href="/notes#%d">Unbenannt</a>' % k4                   # von Hand geaendert
    req("PATCH", f"/api/notes/{par}", {"body": json.dumps({"blocks": bl}), "format": "blocks"})
    rv = get(par)["rev"]
    req("PATCH", f"/api/notes/{k4}", {"title": "Hand"})
    ok("Von Hand geaenderter Absatz: bleibt", get(par)["rev"] == rv and blocks(par)[i]["data"]["text"].startswith("Siehe "))
    st, d = new_sub(par, "")
    k5 = d["id"]
    with connect() as c, c:
        c.execute("UPDATE note SET created=created-700 WHERE id=?", (k5,))
    rv = get(par)["rev"]
    req("PATCH", f"/api/notes/{k5}", {"title": "Spaet"})
    ok("Nach 10 Minuten benannt: bleibt 'Unbenannt' (nur Anzeige zieht nach)", get(par)["rev"] == rv
       and "Unbenannt" in blocks(par)[links_to(blocks(par), k5)[0]]["data"]["text"])
    st, d = new_sub(par, "")
    k6 = d["id"]
    req("PATCH", f"/api/notes/{par}", {"locked": True})
    rv = get(par)["rev"]
    req("PATCH", f"/api/notes/{k6}", {"title": "Hinter Schloss"})
    ok("Gesperrte Elternseite: bleibt", get(par)["rev"] == rv)
    req("PATCH", f"/api/notes/{par}", {"locked": False})
    # spaetere Umbenennung nach dem ersten Titel innerhalb des Fensters zieht weiter nach (Etappen), danach nicht
    with connect() as c, c:
        c.execute("UPDATE note SET created=created-700 WHERE id=?", (kid,))
    rv = get(par)["rev"]
    req("PATCH", f"/api/notes/{kid}", {"title": "Ganz anders"})
    ok("Spaetere Umbenennung (nach dem Fenster): Verweistext bleibt", get(par)["rev"] == rv and "Neues Projekt" in blocks(par)[1]["data"]["text"])


def t_apps():
    print("--- Aeltere Apps setzen den Verweis selbst (iOS/Android/Linux \"/Seite\") ---")
    par = page("App-Eltern", [{"id": "a1", "type": "paragraph", "data": {"text": "oben"}}, {"id": "x1", "type": "paragraph", "data": {"text": ""}},
                              {"id": "x2", "type": "paragraph", "data": {"text": ""}}])
    g = get(par)
    rev, mine = g["rev"], json.loads(g["body"])["blocks"]
    kid = new_sub(par, "")[1]["id"]
    dbid = new_sub(par, "Neue Datenbank", kind="database", icon="🗃️")[1]["id"]
    ok("(Ausgangslage) Server setzte zwei automatische Absaetze", len(links_to(blocks(par), kid)) == 1 and len(links_to(blocks(par), dbid)) == 1)
    mine[1] = {"id": "x1", "type": "paragraph", "data": {"text": '📄 <a href="/notes#%d">Unbenannt</a>' % kid}}      # wie iOS/Android/Linux
    mine[2] = {"id": "x2", "type": "database", "data": {"db": dbid}}
    st, d = req("PATCH", f"/api/notes/{par}", {"body": json.dumps({"blocks": mine}), "format": "blocks", "rev": rev, "merge": 1})
    bl = blocks(par)
    ok("App (alte rev, merge): nur der eigene Verweis und der eigene Datenbank-Block bleiben", st == 200 and [b["id"] for b in bl] == ["a1", "x1", "x2"]
       and len(links_to(bl, kid)) == 1 and not any(b["id"].startswith("sub") for b in bl), (st, bl))
    ok("Antwort (zusammengefuehrt) ohne den automatischen Absatz", "subu-" not in (d.get("body") or "") and "sub-" not in (d.get("body") or ""), d)
    # Client hatte den automatischen Absatz schon (aktuelle rev, ohne merge) und setzt seinen eigenen dazu
    par2 = page("App-Eltern 2", [P("oben")])
    k2 = new_sub(par2, "")[1]["id"]
    g = get(par2)
    cur = json.loads(g["body"])["blocks"]
    st, d = req("PATCH", f"/api/notes/{par2}", {"body": json.dumps({"blocks": cur + [{"id": "y1", "type": "paragraph", "data": {
        "text": '📄 <a href="/notes#%d">Unbenannt</a>' % k2}}]}), "format": "blocks", "rev": g["rev"]})
    ok("App (aktuelle rev): automatischer Absatz faellt weg", st == 200 and len(links_to(blocks(par2), k2)) == 1 and links_to(blocks(par2), k2) == [1], blocks(par2))
    st, d = req("PATCH", f"/api/notes/{par2}", {"body": json.dumps({"blocks": cur + [{"id": "y1", "type": "paragraph", "data": {
        "text": '📄 <a href="/notes#%d">Unbenannt</a> mehr' % k2}}]}), "format": "blocks"})
    ok("Veralteter Client schickt ihn noch einmal mit: bleibt weg", st == 200 and len(links_to(blocks(par2), k2)) == 1, blocks(par2))
    # Gegenproben
    par3 = page("App-Eltern 3", [P("oben")])
    k3 = new_sub(par3, "Normal")[1]["id"]
    bl = blocks(par3)
    bl[0]["data"]["text"] = "oben geaendert"
    req("PATCH", f"/api/notes/{par3}", {"body": json.dumps({"blocks": bl}), "format": "blocks"})
    ok("Gegenprobe: normales Speichern ohne zweiten Verweis -> automatischer Absatz bleibt", links_to(blocks(par3), k3) == [1])
    # pruef-ulink: Verweis im Fliesstext gleich nach dem Anlegen (innerhalb 10 Minuten) ist kein App-Verweis -> beide bleiben
    bl = blocks(par3) + [P('Siehe <a href="/notes#%d">Normal</a> fuer Details.' % k3), P('Siehe <a href="/notes#%d">Normal</a>' % k3)]
    req("PATCH", f"/api/notes/{par3}", {"body": json.dumps({"blocks": bl}), "format": "blocks", "rev": get(par3)["rev"]})
    bl = blocks(par3)
    ok("Gegenprobe: Verweis im Fliesstext gleich nach dem Anlegen -> automatischer Absatz bleibt", len(links_to(bl, k3)) == 3
       and any(str(b.get("id", "")).startswith("sub-") for b in bl), bl)
    bl = [b for b in bl if not (b.get("data") or {}).get("text", "").startswith("Siehe")]
    req("PATCH", f"/api/notes/{par3}", {"body": json.dumps({"blocks": bl}), "format": "blocks"})
    with connect() as c, c:
        c.execute("UPDATE note SET created=created-700 WHERE id=?", (k3,))
    bl = blocks(par3) + [P('Siehe <a href="/notes#%d">Normal</a>' % k3)]
    req("PATCH", f"/api/notes/{par3}", {"body": json.dumps({"blocks": bl}), "format": "blocks"})
    ok("Gegenprobe: spaeter (nach 10 Minuten) ein Verweis im Text dazu -> beide bleiben", len(links_to(blocks(par3), k3)) == 2, blocks(par3))


def t_dup():
    print("--- Duplizieren ---")
    par = page("Dup-Eltern", [P("Kopf"), P("Ende")])
    st, d = new_sub(par, "Original")
    orig = d["id"]
    bl = blocks(par)
    ok("(Ausgangslage) Verweis auf Original am Ende", links_to(bl, orig) == [2], bl)
    # Original-Verweis nach vorn ruecken (Nutzer hat umgestellt)
    bl = [bl[0], bl[2], bl[1]]
    req("PATCH", f"/api/notes/{par}", {"body": json.dumps({"blocks": bl}), "format": "blocks"})
    st, d = req("POST", f"/api/notes/{orig}/duplicate", {"parent_id": par, "title": "Original (Kopie)"})
    cp = d["id"]
    bl = blocks(par)
    ok("Kopie direkt hinter dem Original-Verweis (wie Notion)", links_to(bl, orig) == [1] and links_to(bl, cp) == [2] and bl[3]["data"]["text"] == "Ende", bl)
    # Original ohne Verweis in der Elternseite -> Kopie ans Ende
    st, d = new_sub(par, "Still", link=False)
    still = d["id"]
    st, d = req("POST", f"/api/notes/{still}/duplicate", {"parent_id": par})
    bl = blocks(par)
    ok("Original ohne Verweis: Kopie ans Ende", links_to(bl, d["id"]) == [len(bl) - 1], bl)
    # Neue Seite aus eigener Vorlage (is_template) unter dieser Elternseite -> ans Ende, nicht hinter die Vorlage
    st, d = new_sub(par, "Meine Vorlage")
    tpl = d["id"]
    req("PATCH", f"/api/notes/{tpl}", {"template": True})
    req("PATCH", f"/api/notes/{par}", {"body": json.dumps({"blocks": [P("Kopf")] + [b for b in blocks(par) if not links_to([b], tpl)][:1]
                                                                     + [b for b in blocks(par) if links_to([b], tpl)] + [P("Schluss")]}), "format": "blocks"})
    st, d = req("POST", f"/api/notes/{tpl}/duplicate", {"parent_id": par})
    bl = blocks(par)
    ok("Aus Vorlage: ans Ende (nicht hinter den Vorlagen-Verweis)", links_to(bl, d["id"]) == [len(bl) - 1] and bl[-2]["data"]["text"] == "Schluss", bl)
    # Duplikat in eine Datenbank: nichts
    dbid = page("Dup-DB", kind="database")
    rv = get(dbid)["rev"]
    req("POST", f"/api/notes/{orig}/duplicate", {"parent_id": dbid})
    ok("Duplikat in Datenbank: Datenbank unveraendert", get(dbid)["rev"] == rv)
    # link false
    n = len(blocks(par))
    req("POST", f"/api/notes/{orig}/duplicate", {"parent_id": par, "link": False})
    ok("Duplizieren mit link: false -> kein Verweis", len(blocks(par)) == n)


def t_gallery():
    print("--- Galerie ---")
    top = req("POST", "/api/gallery/wiki/use?lang=de", {"parent_id": None, "examples": True})[1]
    ok("(Ausgangslage) Wiki oben angelegt", isinstance(top, dict) and top.get("root"), top)
    par = page("Arbeit", [P("Bereich")])
    st, d = req("POST", "/api/gallery/wiki/use?lang=de", {"parent_id": par, "examples": True})
    ok("Wiki unter Seite angelegt", st == 200 and d.get("root"), d)
    bl = blocks(par)
    ok("Genau ein Verweis (Startseite der Vorlage) am Ende", len(bl) == 2 and is_link_para(bl[1], d["root"]), bl)
    others = [x for x in d["created"] if x != d["root"]]
    ok("Keine weiteren Vorlagen-Seiten in der Elternseite", not any(links_to(bl, x) for x in others))
    ok("Innenleben unveraendert (Startseite gleich wie oben angelegt)",
       [b.get("type") for b in blocks(d["root"])] == [b.get("type") for b in blocks(top["root"])], (blocks(d["root"])[:3], blocks(top["root"])[:3]))
    n = len(blocks(par))
    req("POST", "/api/gallery/wiki/use?lang=de", {"parent_id": par, "examples": False, "link": False})
    ok("Galerie mit link: false -> kein Verweis", len(blocks(par)) == n)


def t_move():
    print("--- Verschieben ---")
    a = page("Alt", [P("oben")])
    b_ = page("Neu", [P("Ziel")])
    st, d = new_sub(a, "Wanderer")
    w = d["id"]
    # Fliesstext-Verweis zusaetzlich (bleibt)
    bl = blocks(a) + [P('Siehe auch <a href="/notes#%d">Wanderer</a> hier.' % w)]
    req("PATCH", f"/api/notes/{a}", {"body": json.dumps({"blocks": bl}), "format": "blocks"})
    st, d = req("POST", f"/api/notes/{w}/move", {"parent_id": b_, "index": 0})
    ok("Verschieben ok", st == 200, d)
    ba, bb = blocks(a), blocks(b_)
    ok("Alte Elternseite: Verweis-Absatz weg, Fliesstext-Verweis bleibt", [x["data"]["text"][:9] for x in ba] == ["oben", "Siehe auc"], ba)
    ok("Neue Elternseite: Verweis am Ende", len(bb) == 2 and is_link_para(bb[1], w), bb)
    rv = get(b_)["rev"]
    st, d = req("POST", f"/api/notes/{w}/move", {"parent_id": b_, "index": 0})
    ok("Nur umsortieren: nichts", get(b_)["rev"] == rv)
    # pruef-ulink: von Hand gesetzter reiner Verweis-Absatz (eigene ID) bleibt beim Verschieben in der alten Elternseite
    h1, h2 = page("AltH", [P("oben")]), page("NeuH", [P("Ziel")])
    hk = page("KindH", None, parent=h1)
    man = {"id": "manuell1", "type": "paragraph", "data": {"text": '<a href="/notes#%d" class="note-link">📄 KindH</a>' % hk}}
    req("PATCH", f"/api/notes/{h1}", {"body": json.dumps({"blocks": [P("oben"), man]}), "format": "blocks"})
    req("POST", f"/api/notes/{hk}/move", {"parent_id": h2, "index": 0})
    ok("Von Hand gesetzter Verweis-Absatz bleibt in der alten Elternseite", any(x.get("id") == "manuell1" for x in blocks(h1)), blocks(h1))
    ok("... neue Elternseite bekommt trotzdem den automatischen Verweis", links_to(blocks(h2), hk) == [1], blocks(h2))
    # zurueck nach a (dort steht schon ein Fliesstext-Verweis) -> kein zweiter
    st, d = req("POST", f"/api/notes/{w}/move", {"parent_id": a, "index": 0})
    ok("Zurueck: vorhandener Verweis zaehlt -> kein neuer; aus Neu entfernt",
       len(blocks(a)) == 2 and not links_to(blocks(b_), w), (blocks(a), blocks(b_)))
    # PATCH parent_id
    st, d = req("PATCH", f"/api/notes/{w}", {"parent_id": b_})
    ok("PATCH parent_id: Verweis in Neu", st == 200 and links_to(blocks(b_), w) == [1], blocks(b_))
    # nach oben
    st, d = req("POST", f"/api/notes/{w}/move", {"parent_id": None, "index": 0})
    ok("Nach oben: aus Neu entfernt", not links_to(blocks(b_), w) and len(blocks(b_)) == 1, blocks(b_))
    # gesperrte alte Elternseite bleibt
    st, d = new_sub(b_, "Zweiter")
    z = d["id"]
    req("PATCH", f"/api/notes/{b_}", {"locked": True})
    rv = get(b_)["rev"]
    req("POST", f"/api/notes/{z}/move", {"parent_id": a, "index": 0})
    ok("Gesperrte alte Elternseite: Verweis bleibt; neue bekommt ihn", get(b_)["rev"] == rv and links_to(blocks(b_), z) and links_to(blocks(a), z),
       (blocks(b_), blocks(a)))
    req("PATCH", f"/api/notes/{b_}", {"locked": False})
    # link false beim Verschieben
    rv = get(b_)["rev"]
    req("POST", f"/api/notes/{z}/move", {"parent_id": b_, "index": 0, "link": False})
    ok("Verschieben mit link: false -> keine Inhaltsaenderung", get(b_)["rev"] == rv)


def t_conflict():
    print("--- Konfliktweg ---")
    par = page("Offen", [P("eins"), P("zwei")])
    g = get(par)
    rev, bl = g["rev"], json.loads(g["body"])["blocks"]
    st, d = new_sub(par, "Waehrenddessen")
    kid = d["id"]
    mine = [dict(bl[0], data={"text": "eins geaendert"}), bl[1]]
    st, d = req("PATCH", f"/api/notes/{par}", {"body": json.dumps({"blocks": mine}), "format": "blocks", "rev": rev})
    ok("Alte rev ohne merge -> 409", st == 409, (st, d))
    st, d = req("PATCH", f"/api/notes/{par}", {"body": json.dumps({"blocks": mine}), "format": "blocks", "rev": rev, "merge": 1})
    bl2 = blocks(par)
    ok("Alte rev mit merge: eigene Aenderung und Verweis bleiben", st == 200 and bl2[0]["data"]["text"] == "eins geaendert"
       and links_to(bl2, kid) == [2], (st, d, bl2))
    st, d = req("GET", f"/api/notes/{par}/versions")
    ok("Version der Elternseite vorhanden", st == 200 and isinstance(d, (dict, list)), d)


def t_api():
    print("--- API v1 / MCP ---")
    par = page("API-Eltern", [P("x")])
    st, d = req("POST", "/api/account/api-tokens", {"name": "ulink", "scopes": ["write:pages", "mcp"]})
    ok("(Ausgangslage) Token", st == 200 and d.get("token"), d)
    tok = d.get("token", "")
    st, d = req("POST", "/api/v1/pages", {"parent_id": par, "title": "Per API", "markdown": "Hallo"}, bearer=tok)
    ok("API v1: Verweis in der Elternseite", st == 201 and links_to(blocks(par), d["id"]) == [1], (st, d, blocks(par)))
    st, d = req("POST", "/api/v1/pages", {"parent_id": par, "title": "Ohne", "link": False}, bearer=tok)
    ok("API v1 link: false -> kein Verweis", st == 201 and not links_to(blocks(par), d["id"]), (st, d))
    st, d = req("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "create_page", "arguments":
                                 {"title": "Per MCP", "parent_id": par, "markdown": "Text"}}}, bearer=tok, hdr={"MCP-Protocol-Version": "2025-06-18"})
    txt = "".join(c.get("text", "") for c in ((d.get("result") or {}).get("content") or [])) if isinstance(d, dict) else ""
    m = re.search(r'\bID (\d+)', txt)
    mid = int(next(g for g in m.groups() if g)) if m else 0
    ok("MCP create_page: Verweis in der Elternseite", st == 200 and mid and links_to(blocks(par), mid), (st, txt[:300], blocks(par)))
    for x in (req("GET", "/api/account/api-tokens")[1] or {}).get("tokens", []):
        req("DELETE", f"/api/account/api-tokens/{x['id']}")


def t_import():
    print("--- Import ---")
    par = page("Import-Ziel", [P("Ziel")])
    st, d = req("POST", "/api/import/upload?name=Notizen.zip&replace=1", raw=zipped({
        "Notizen/Eins.md": "# Eins\n\nSiehe [[Zwei]].\n", "Notizen/Zwei.md": "# Zwei\n\nText\n"}))
    ok("(Ausgangslage) Upload", st == 200 and d.get("token"), d)
    st, r = req("POST", f"/api/import/{d['token']}/run", {"kind": "md", "parent_id": par})
    run = wait_run(r["run_id"]) if st == 202 else {}
    ok("(Ausgangslage) Import fertig", run.get("state") == "done", run)
    bl = blocks(par)
    root = run.get("root")
    ok("Oberste Import-Seite verlinkt (einmal, am Ende)", len(bl) == 2 and is_link_para(bl[1], root), bl)
    with connect() as c:
        kids = [r[0] for r in c.execute("SELECT id FROM note WHERE parent_id=? AND deleted_at IS NULL", (root,)).fetchall()]
    inner = [b for k in kids for b in blocks(root) if links_to([b], k)]
    ok("Innere Seiten nicht zusaetzlich in der Sammelseite verlinkt", all(not is_link_para(b, k) for k in kids for b in blocks(root)), (blocks(root), inner))


def t_live():
    print("--- Live (notes.live) ---")
    from collab_client import Client
    from pycrdt import XmlFragment, XmlText
    live(True)
    cl = None
    try:
        par = page("Live-Eltern", [P("Hallo")])
        cl = Client(WS, {"Cookie": "saga=" + ck(UA)})
        cl.sub(par)
        h = cl.wait("HELLO", par); s2 = cl.wait("SYNC2", par)
        ok("(Ausgangslage) Live-Raum offen", h and h[0] == "HELLO" and s2 and s2[0] == "SYNC2", (h, s2))

        def typ(d):
            el = list(d.get("prosemirror", type=XmlFragment).children)[0]
            next(x for x in el.children if isinstance(x, XmlText)).insert(5, " Welt")
        cl.edit(par, typ)
        time.sleep(0.15)
        st, d = new_sub(par, "Live-Kind")
        kid = d["id"]
        got = cl.wait("UPDATE", par, timeout=8)
        ok("Update an den offenen Raum", got is not None and got[0] == "UPDATE", got)
        time.sleep(0.5)
        doc = cl.blocks(par)[0]
        js = json.dumps(doc, ensure_ascii=False)
        ok("Im Live-Stand: eigene Eingabe UND Verweis", "Hallo Welt" in js and ('/notes#%d\\"' % kid) in js, js[:500])
        cl.close(); cl = None
        t0 = time.time()
        while time.time() - t0 < 8:
            js2 = json.dumps(blocks(par), ensure_ascii=False)
            if "Hallo Welt" in js2 and ('/notes#%d\\"' % kid) in js2:
                break
            time.sleep(0.3)
        ok("Nach dem Materialisieren in note.body: beide", "Hallo Welt" in js2 and ('/notes#%d\\"' % kid) in js2 and len(links_to(blocks(par), kid)) == 1, js2[:400])
    finally:
        if cl:
            cl.close()
        live(False)


try:
    setup()
    cleanup()
    setup()
    for fn in (t_create, t_not, t_widget, t_retitle, t_apps, t_dup, t_gallery, t_move, t_conflict, t_api, t_import, t_live):
        if fn is t_live and os.environ.get("ULINK_NO_LIVE") == "1":
            continue
        try:                             # jeder Abschnitt fuer sich (Gegenprobe gegen alten Stand: alle Abschnitte melden)
            fn()
        except Exception as ex:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            ok(f"{fn.__name__}: Ablauf ohne Ausnahme", False, ex)
except Exception as ex:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    ok("Ablauf ohne Ausnahme", False, ex)
finally:
    cleanup()

ok("Serverfehler: 0", n500[0] == 0, n500[0])
print(f"Serverfehler: {n500[0]}")
print(f"{sum(res)}/{len(res)}", "ALLES GRUEN" if all(res) else "FEHLER")
sys.exit(0 if all(res) else 1)
