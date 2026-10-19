"""Nextcloud-Dateien in Seiten (2.3, saga/nextcloud.py) gegen nachgemachte Nextclouds (tests/fakedav.py) und die laufende
Test-Instanz (HK_TEST_BASE); Foederation mit zwei eigenen Instanzen (wie tests/foederation_teilen.py).

Prueft: SSRF (http/intern ohne Freigabe abgewiesen), Verknuepfen per Login Flow v2 (App-Passwort verschluesselt), Auswahl
(Ordner, Suche, Zuletzt, Link-Erkennung, Vorschau), Block speichern (author/sig setzt der Server), Vorschau und Download fuer
Leser OHNE eigenes Nextcloud, KEIN Zugriff fuer Nicht-Leser, Mitbearbeiter kann nicht ueber fremdes Konto einfuegen
(author-Faelschung, fremde Datei, fremde Nextcloud, Schalter fremder Bloecke abgewiesen; bestehende Bloecke bleiben
unveraendert), Kopie in eine andere Seite zeigt nichts, Cache (Platte, LRU), "Als Kopie uebernehmen", Export (Link),
Suche (Name), veroeffentlichte Seite nur mit Schalter, Foederation nur mit Schalter (Autor nur als Platzhalter),
Admin-Schalter, Trennen -> Karte ohne Inhalt (App-Passwort widerrufen), Kontoloeschung."""
import base64
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("SAGA_SKIP_BACKUP", "1")
from itsdangerous import TimestampSigner        # noqa: E402

import auth                                      # noqa: E402
import db                                        # noqa: E402
import fakedav                                   # noqa: E402

B = os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8080")
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
STATE = os.environ["STATE_DIR"]
A, BB = "hk-test-a", "hk-test-b"
TAG = secrets.token_hex(3)
C, D = "hk-test-ncc" + TAG, "hk-test-ncd" + TAG          # C: Leserin ohne Nextcloud, D: ohne Zugriff
P1, P2 = 18341, 18342
NC1, NC2 = fakedav.start(P1, True), fakedav.start(P2, True)
U1, U2 = f"http://127.0.0.1:{P1}", f"http://127.0.0.1:{P2}"
PNG = fakedav.PNG1
res = []


def ok(n, c, info=None):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, "" if c or info is None else f"-> {info}", flush=True)


def cookie(uid):
    return "saga=" + TimestampSigner(os.environ["SESSION_SECRET"]).sign(base64.b64encode(json.dumps({"uid": uid}).encode())).decode()


def call(m, p, b=None, uid=A, api=True, raw=False):
    h = {"Host": HOST}
    if uid:
        h["Cookie"] = cookie(uid)
    if api:
        h["X-Saga"] = "1"
    data = None
    if b is not None:
        data, h["Content-Type"] = json.dumps(b).encode(), "application/json"
    req = urllib.request.Request(B + p, data=data, method=m, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body, st, hd = r.read(), r.status, {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        body, st, hd = e.read(), e.code, {k.lower(): v for k, v in e.headers.items()}
    if raw:
        return st, body, hd
    try:
        return st, json.loads(body)
    except ValueError:
        return st, body


def q(sql, args=()):
    with db.connect() as c, c:
        cur = c.execute(sql, args)
        try:
            return cur.fetchall()
        except Exception:  # noqa: BLE001
            return None


def setting(k, v):
    auth.save_setting(k, v)
    time.sleep(5.5)                                  # Einstellungs-Cache des Servers (5 s)


def put(base, path, data):
    req = urllib.request.Request(base + "/_put?" + urllib.parse.urlencode({"f": path}), data=data, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=10).read())["fileid"]


def stats(base):
    return json.loads(urllib.request.urlopen(base + "/_stats", timeout=10).read())


def doc(*bs):
    return json.dumps({"time": 1, "version": "2.30.7", "blocks": [dict({"id": f"b{i}"}, **b) for i, b in enumerate(bs)]})


def para(t):
    return {"type": "paragraph", "data": {"text": t}}


def ncb(fid, inst, **kw):
    d = {"inst": inst, "fileid": str(fid), "kind": kw.pop("kind", "file"), "name": kw.pop("name", "datei"), "mime": kw.pop("mime", ""),
         "size": kw.pop("size", 1), "mtime": 1, "public": False, "fed": False}
    d.update(kw)
    return {"type": "nextcloud", "data": d}


def body_of(nid):
    return json.loads(q("SELECT body FROM note WHERE id=?", (nid,))[0][0])


def ncdata(nid):
    out = []

    def walk(bs):
        for b in bs:
            if b.get("type") == "nextcloud":
                out.append(b["data"])
            d = b.get("data") or {}
            walk(d.get("blocks") or [])
            for c in d.get("cols") or []:
                walk(c.get("blocks") or [])
    walk(body_of(nid)["blocks"])
    return out


def save(nid, body, uid=A):
    return call("PATCH", f"/api/notes/{nid}", {"body": body, "format": "blocks"}, uid=uid)


def connect(uid, base):
    s, d = call("POST", "/api/settings/nextcloud", {"url": base}, uid=uid)
    if s != 200:
        return s, d
    urllib.request.urlopen(base + "/_grant", timeout=10).read()
    for _ in range(30):
        time.sleep(0.5)
        s2, st = call("GET", "/api/settings/nextcloud", uid=uid)
        if st.get("connected"):
            return 200, st
    return 0, st


# =====================================================================================================
def part_connect():
    for u in (C, D):
        auth.get_user(u) or auth.create_user(u, None, u)
    setting("nc_files", True)
    s, d = call("GET", "/api/settings/nextcloud")
    ok(f"Einstellungen: noch nicht verbunden ({s})", s == 200 and d["enabled"] and not d["connected"] and d["me"] == A)
    # SSRF: ohne Freigabe nur https + oeffentliche Adressen
    s, d = call("POST", "/api/settings/nextcloud", {"url": U1})
    ok(f"SSRF: http auf internen Host ohne Freigabe abgewiesen ({s})", s == 400 and "https" in str(d))
    s, d = call("POST", "/api/settings/nextcloud", {"url": f"https://127.0.0.1:{P1}"})
    ok(f"SSRF: https auf interne Adresse ohne Freigabe abgewiesen ({s})", s == 400 and "intern" in str(d.get("error", "")))
    s, d = call("POST", "/api/settings/nextcloud", {"url": "https://169.254.169.254"})
    ok(f"SSRF: Cloud-Metadaten-Adresse abgewiesen ({s})", s == 400)
    s, d = call("POST", "/api/settings/nextcloud", {"url": "javascript:alert(1)"})
    ok(f"Ungueltige Adresse abgewiesen ({s})", s == 400)
    hosts = auth.settings(fresh=True).get("allowed_hosts") or ""
    setting("allowed_hosts", hosts + f", 127.0.0.1:{P1}, 127.0.0.1:{P2}")
    s, st = connect(A, U1)
    ok(f"A: verbunden per Login Flow v2 ({s})", s == 200 and st["server"] == U1 and st["user"] == fakedav.LOGIN)
    row = q("SELECT secret, dav_user FROM nc_account WHERE uid=?", (A,))
    ok("A: App-Passwort verschluesselt gespeichert (nicht im Klartext), Benutzer-ID aus OCS",
       row and row[0][1] == fakedav.UID and NC1.store.pw[fakedav.LOGIN] not in row[0][0])
    s, st = connect(BB, U2)
    ok(f"B: eigene Nextcloud verbunden ({s})", s == 200 and st["server"] == U2)
    s, d = call("GET", "/api/settings/nextcloud", uid=C)
    ok("C: nicht verbunden", s == 200 and not d["connected"])


def part_pick():
    ids = {"foto": put(U1, "Bilder/foto.png", PNG), "bericht": put(U1, "Dokumente/bericht.pdf", b"%PDF-1.4 bericht " + b"x" * 500),
           "geheim": put(U1, "geheim.txt", b"streng geheim"), "notiz": put(U1, "Dokumente/notiz.txt", b"Notiz-Inhalt 123")}
    ids["bfile"] = put(U2, "b-eigen.txt", b"Datei von B")
    ids["ordner"] = json.loads(urllib.request.urlopen(U1 + "/_ids").read())["/Dokumente"]
    s, d = call("GET", "/api/nextcloud/list?path=/")
    names = {x["name"]: x for x in d.get("items", [])}
    ok(f"Auswahl: Wurzel listet Ordner und Dateien ({s})", s == 200 and names.get("Bilder", {}).get("dir") and "geheim.txt" in names
       and names["Bilder"]["kind"] == "folder")
    s, d = call("GET", "/api/nextcloud/list?path=/Bilder")
    it = (d.get("items") or [{}])[0]
    ok(f"Auswahl: Unterordner mit Datei-ID, Typ, Groesse ({s})", s == 200 and it.get("fileid") == str(ids["foto"]) and it.get("kind") == "image"
       and it.get("mime") == "image/png" and it.get("size") == len(PNG) and it.get("inst") == U1)
    s, d = call("GET", "/api/nextcloud/list?path=/../../etc")
    ok(f"Auswahl: Pfad mit .. bleibt im eigenen Bereich ({s})", s in (200, 404))
    s, d = call("GET", "/api/nextcloud/search?q=bericht")
    ok(f"Suche nach Namen ({s})", s == 200 and [x["name"] for x in d["items"]] == ["bericht.pdf"])
    s, d = call("GET", "/api/nextcloud/search")
    ok(f"Zuletzt geaendert: neueste zuerst, ohne Ordner ({s})", s == 200 and d["items"] and d["items"][0]["name"] == "notiz.txt"
       and not any(x["dir"] for x in d["items"]))
    s, d = call("GET", "/api/nextcloud/search?q=x", uid=C)
    ok(f"Auswahl ohne Verknuepfung: 409 ({s})", s == 409)
    for url, want in ((f"{U1}/f/{ids['foto']}", True), (f"{U1}/index.php/f/{ids['foto']}", True),
                      (f"{U1}/apps/files/?dir=/Bilder&fileid={ids['foto']}", True), (f"{U1}/apps/files/files/{ids['foto']}?dir=/", True),
                      (f"{U2}/f/{ids['bfile']}", False), ("https://example.org/f/1", False)):
        s, d = call("POST", "/api/nextcloud/resolve", {"url": url})
        ok(f"Link-Erkennung {url.split('://')[1][:40]} -> {want} ({s})", s == 200 and d.get("match") is want
           and (not want or (d["block"]["fileid"] == str(ids["foto"]) and d["block"]["kind"] == "image" and "author" not in d["block"])))
    s, d = call("POST", "/api/nextcloud/resolve", {"fileid": "999999"})
    ok(f"Unbekannte Datei-ID: 404 ({s})", s == 404)
    s, body, h = call("GET", f"/api/nextcloud/thumb/{ids['foto']}", raw=True)
    ok(f"Auswahl: Vorschau (eigenes Konto) ({s})", s == 200 and body.startswith(b"\x89PNG") and h.get("x-content-type-options") == "nosniff")
    s, _, _ = call("GET", f"/api/nextcloud/thumb/{ids['foto']}", raw=True, api=False)
    ok(f"Auswahl-Vorschau nur mit X-Saga ({s})", s == 401)
    return ids


def part_blocks(ids):
    s, d = call("POST", "/api/notes", {"title": "NC-Seite " + TAG})
    P = d["id"]
    for u, role in ((BB, "edit"), (C, "view")):
        s, _ = call("PUT", f"/api/notes/{P}/shares", {"type": "user", "target": u, "role": role})
        ok(f"Seite geteilt mit {u[:12]} ({role}) ({s})", s == 200)
    body = doc(para("Start"), ncb(ids["foto"], U1, kind="image", name="foto.png", mime="image/png"),
               {"type": "columns", "data": {"cols": [{"blocks": [ncb(ids["bericht"], U1, name="bericht.pdf", mime="application/pdf",
                                                                     author="hk-falsch")]}, {"blocks": []}]}})
    s, d = save(P, body)
    ok(f"A: Block mit fremdem author (verschachtelt) abgewiesen ({s})", s == 403)
    body = doc(para("Start"), ncb(ids["foto"], U1, kind="image", name="foto.png", mime="image/png"),
               {"type": "columns", "data": {"cols": [{"blocks": [ncb(ids["bericht"], U1, name="bericht.pdf", mime="application/pdf", author=A)]},
                                                     {"blocks": [ncb(ids["ordner"], U1, kind="folder", name="Dokumente")]}]}},
               ncb(ids["foto"], U1, kind="image", name="foto.png", author=A, sig="0" * 32))
    s, d = save(P, body)
    nd = ncdata(P)
    ok(f"A: Bloecke gespeichert, author/sig vom Server ({s})", s == 200 and len(nd) == 4 and all(x["author"] == A and len(x["sig"]) == 32 for x in nd))
    ok("Signatur vom Client ersetzt", nd[3]["sig"] != "0" * 32)
    s, d = save(P, doc(ncb(ids["foto"], U2, kind="image")))
    ok(f"A: Datei einer anderen Nextcloud als der eigenen abgewiesen ({s})", s == 400)
    s, d = call("GET", f"/api/notes/{P}")
    s2, d2 = call("GET", f"/api/notes/{P}", uid=C)
    ok("Leserin sieht den Block (Name/Typ; Autor nur als Anmeldename auf derselben Instanz)", s2 == 200 and '"nextcloud"' in d2["body"])
    return P


def part_read(P, ids):
    base = f"/api/notes/{P}/nc"
    s, d = call("GET", base, uid=C)
    items = {x["fileid"]: x for x in d.get("items", [])}
    ok(f"Leserin ohne Nextcloud: Status aller Bloecke ({s})", s == 200 and items.get(str(ids["foto"]), {}).get("ok")
       and items[str(ids["bericht"])]["name"] == "bericht.pdf" and items[str(ids["ordner"])].get("dir"))
    ok("Ordner: erste Eintraege", {e["name"] for e in items[str(ids["ordner"])].get("entries", [])} == {"bericht.pdf", "notiz.txt"})
    n0 = stats(U1).get("previews", 0)
    s, body, h = call("GET", f"{base}/{ids['foto']}?a={A}&w=640", uid=C, api=False, raw=True)
    ok(f"Leserin: Vorschau ueber Saga (Konto des Autors) ({s})", s == 200 and body.startswith(b"\x89PNG") and "private" in h.get("cache-control", "")
       and h.get("content-security-policy", "").startswith("default-src 'none'"))
    s, body, h = call("GET", f"{base}/{ids['bericht']}?a={A}&dl=1", uid=C, api=False, raw=True)
    ok(f"Leserin: Download ({s})", s == 200 and body.startswith(b"%PDF-1.4 bericht") and h.get("content-type") == "application/octet-stream"
       and "attachment" in h.get("content-disposition", ""))
    n1 = stats(U1).get("previews", 0)
    s, body, _ = call("GET", f"{base}/{ids['foto']}?a={A}&w=640", uid=BB, api=False, raw=True)
    ok(f"Cache: zweiter Abruf ohne neue Vorschau bei Nextcloud ({n1 - n0} -> {stats(U1).get('previews', 0) - n0})",
       s == 200 and stats(U1).get("previews", 0) == n1 and n1 == n0 + 1)
    cache = os.path.join(STATE, "nc-cache")
    ok("Cache liegt auf Platte (nc-cache, nicht in files/)", os.path.isdir(cache) and len(os.listdir(cache)) >= 1)
    from saga import nextcloud as ncm
    ok("Cache: LRU-Aufraeumen auf Grenze 0 loescht alles", ncm.prune_cache(0) >= 1 and not os.listdir(cache))
    s, body, _ = call("GET", f"{base}/{ids['foto']}?a={A}&w=640", uid=C, api=False, raw=True)
    ok("Cache: nach dem Aufraeumen wieder von Nextcloud", s == 200 and stats(U1).get("previews", 0) == n1 + 1)
    # Nicht-Leser / falsche Angaben
    s, _, _ = call("GET", f"{base}/{ids['foto']}?a={A}&w=640", uid=D, api=False, raw=True)
    ok(f"Ohne Leserecht: kein Zugriff ({s})", s == 404)
    s, d = call("GET", base, uid=D)
    ok(f"Ohne Leserecht: kein Status ({s})", s == 404)
    s, _, _ = call("GET", f"{base}/{ids['foto']}?a={A}&w=640", uid=None, api=False, raw=True)
    ok(f"Ohne Anmeldung: kein Zugriff ({s})", s == 404)
    s, _, _ = call("GET", f"{base}/{ids['geheim']}?a={A}&dl=1", uid=C, api=False, raw=True)
    ok(f"Datei, die nicht in der Seite steht: kein Zugriff, auch fuer Leser ({s})", s == 404)
    s, _, _ = call("GET", f"{base}/{ids['foto']}?a={BB}&w=640", uid=C, api=False, raw=True)
    ok(f"Falscher Autor: kein Zugriff ({s})", s == 404)
    s, d = call("GET", f"/api/notes/{P}/export")
    md = d if isinstance(d, (bytes, str)) else json.dumps(d)
    md = md.decode() if isinstance(md, bytes) else md
    ok(f"Export: Link statt Kopie ({s})", f"{U1}/f/{ids['bericht']}" in md and "bericht.pdf" in md)
    def hits(term):
        s, d = call("POST", "/api/notes/search", {"q": term})
        return [x.get("id") for x in (d.get("items") if isinstance(d, dict) else d) or []] if s == 200 else []
    found = False
    for _ in range(20):
        found = P in hits("bericht")
        if found:
            break
        time.sleep(0.5)
    ok("Suche: Dateiname im Index", found)
    ok("Suche: Adresse/Kennungen nicht im Index", P not in hits("127.0.0.1") and P not in hits(A))


def part_editor(P, ids):
    """B bearbeitet A's Seite: eigene Dateien ja, fremde nie."""
    cur = body_of(P)
    a_blocks = json.loads(json.dumps(cur["blocks"]))
    before = ncdata(P)
    # 1) unveraendert zurueck (wie eine App, die den Block nicht kennt) -> ok, A's Bloecke unveraendert
    s, d = save(P, json.dumps({"blocks": a_blocks + [para("von B")]}), uid=BB)
    ok(f"B: speichert Seite mit A's Bloecken unveraendert ({s})", s == 200 and ncdata(P) == before, (before, ncdata(P), d))
    # 2) ohne Signatur (wie ein Client, der sie nicht kennt) -> gespeicherte Fassung bleibt
    nosig = json.loads(json.dumps(a_blocks))
    nosig[1]["data"].pop("sig")
    nosig[1]["data"]["name"] = "umbenannt.png"
    s, d = save(P, json.dumps({"blocks": nosig}), uid=BB)
    ok(f"B: A's Block ohne Signatur/mit anderem Namen -> gespeicherte Fassung bleibt ({s})", s == 200 and ncdata(P)[0]["name"] == "foto.png"
       and ncdata(P)[0]["sig"] == a_blocks[1]["data"]["sig"])
    # 3) Faelschungen
    s, d = save(P, json.dumps({"blocks": a_blocks + [ncb(ids["geheim"], U1, name="geheim.txt", author=A)]}), uid=BB)
    ok(f"B: neuer Block mit author=A (A's geheime Datei) abgewiesen ({s})", s == 403)
    s, d = save(P, json.dumps({"blocks": a_blocks + [ncb(ids["geheim"], U1, name="geheim.txt")]}), uid=BB)
    ok(f"B: Datei aus A's Nextcloud ohne author abgewiesen (nicht B's Nextcloud) ({s})", s == 400)
    s, d = save(P, json.dumps({"blocks": a_blocks + [dict(ncb(ids["geheim"], U1, name="geheim.txt", author=A), )]}), uid=C)
    ok(f"C (Leserin): darf gar nicht speichern ({s})", s == 403)
    sw = json.loads(json.dumps(a_blocks))
    sw[1]["data"]["public"] = True
    s, d = save(P, json.dumps({"blocks": sw}), uid=BB)
    ok(f"B: Schalter 'veroeffentlicht' an A's Block abgewiesen ({s})", s == 403 and ncdata(P)[0]["public"] is False)
    fid = json.loads(json.dumps(a_blocks))
    fid[1]["data"]["fileid"] = str(ids["geheim"])
    s, d = save(P, json.dumps({"blocks": fid}), uid=BB)
    ok(f"B: Datei-ID in A's Block getauscht abgewiesen ({s})", s == 403)
    # 4) eigene Datei aus B's Nextcloud
    s, d = save(P, json.dumps({"blocks": a_blocks + [ncb(ids["bfile"], U2, name="b-eigen.txt", mime="text/plain")]}), uid=BB)
    nd = ncdata(P)
    ok(f"B: eigene Datei eingefuegt (author=B, sig) ({s})", s == 200 and nd[-1]["author"] == BB and nd[-1]["inst"] == U2 and len(nd[-1]["sig"]) == 32)
    s, body, _ = call("GET", f"/api/notes/{P}/nc/{ids['bfile']}?a={BB}&dl=1", uid=C, api=False, raw=True)
    ok(f"C: B's Datei ueber B's Konto ({s})", s == 200 and body == b"Datei von B")
    s, body, _ = call("GET", f"/api/notes/{P}/nc/{ids['bfile']}?a={BB}&dl=1", uid=A, api=False, raw=True)
    ok(f"A (Eigentuemer) sieht B's Datei ({s})", s == 200)
    # A entfernt B's Block (Eigentuemer darf loeschen)
    s, d = save(P, json.dumps({"blocks": a_blocks}), uid=A)
    ok(f"A: entfernt B's Block ({s})", s == 200 and all(x["author"] == A for x in ncdata(P)))
    # 5) Kopie eines signierten Blocks in eine andere Seite: B's eigene Seite
    s, d = call("POST", "/api/notes", {"title": "B-Seite " + TAG}, uid=BB)
    PB = d["id"]
    s, d = save(PB, json.dumps({"blocks": [a_blocks[1]]}), uid=BB)
    ok(f"B: A's signierten Block in eigene Seite kopieren abgewiesen ({s})", s == 403)
    with db.connect() as c, c:                      # an der Pruefung vorbei (Import o. ae.): Signatur gilt nicht fuer PB
        c.execute("UPDATE note SET body=? WHERE id=?", (json.dumps({"blocks": [a_blocks[1]]}), PB))
    s, _ = call("PUT", f"/api/notes/{PB}/shares", {"type": "user", "target": A, "role": "edit"}, uid=BB)
    s, _, _ = call("GET", f"/api/notes/{PB}/nc/{ids['foto']}?a={A}&w=320", uid=BB, api=False, raw=True)
    ok(f"Block an der Pruefung vorbei in andere Seite: kein Inhalt (Signatur je Seite) ({s})", s == 404)
    # 6) B verliert Bearbeiten-Recht -> B's Bloecke ohne Inhalt
    s, d = save(P, json.dumps({"blocks": a_blocks + [ncb(ids["bfile"], U2, name="b-eigen.txt")]}), uid=BB)
    call("PUT", f"/api/notes/{P}/shares", {"type": "user", "target": BB, "role": "view"})
    s, _, _ = call("GET", f"/api/notes/{P}/nc/{ids['bfile']}?a={BB}&dl=1", uid=C, api=False, raw=True)
    ok(f"B nur noch Leser: B's Datei ohne Inhalt ({s})", s == 404)
    call("PUT", f"/api/notes/{P}/shares", {"type": "user", "target": BB, "role": "edit"})
    s, _, _ = call("GET", f"/api/notes/{P}/nc/{ids['bfile']}?a={BB}&dl=1", uid=C, api=False, raw=True)
    ok(f"B wieder Bearbeiter: Datei wieder da ({s})", s == 200)
    s, d = save(P, json.dumps({"blocks": a_blocks}), uid=A)
    return a_blocks


def part_copy(P, ids):
    s, d = call("POST", "/api/nextcloud/import", {"fileid": str(ids["foto"])})
    ok(f"Als Kopie uebernehmen: Bild wird Saga-Datei ({s})", s == 200 and d["file"]["url"].startswith("/files/") and d["file"]["mime"] == "image/png")
    fid = d["file"]["url"].split("/")[-1]
    ok("Kopie gehoert A", q("SELECT uid FROM file WHERE id=?", (fid,)) == [[A]] or q("SELECT uid FROM file WHERE id=?", (fid,))[0][0] == A)
    s, d = call("POST", "/api/nextcloud/import", {"fileid": str(ids["bfile"]), "note": P}, uid=BB)
    fid = (d.get("file") or {}).get("url", "/").split("/")[-1]
    ok(f"B: Kopie aus B's Nextcloud in A's Seite -> gehoert A (Eigentuemer) ({s})", s == 200 and q("SELECT uid FROM file WHERE id=?", (fid,))[0][0] == A)
    s, d = call("POST", "/api/nextcloud/import", {"fileid": str(ids["foto"])}, uid=C)
    ok(f"Kopie ohne Verknuepfung: 409 ({s})", s == 409)
    s, d = call("POST", "/api/nextcloud/import", {"fileid": str(ids["ordner"])})
    ok(f"Ordner laesst sich nicht kopieren ({s})", s == 404)


def part_publish(P, ids, a_blocks):
    s, d = call("PUT", "/api/account/public-name", {"name": "ncanna" + TAG})
    s, d = call("PUT", f"/api/notes/{P}/publish", {"slug": "nc-" + TAG})
    url = (d or {}).get("url") or ""
    ok(f"Seite veroeffentlicht ({s})", s == 200 and url)
    path = urllib.parse.urlparse(url).path
    s, html, _ = call("GET", path, uid=None, api=False, raw=True)
    html = html.decode()
    ok(f"Veroeffentlicht ohne Schalter: Block nicht sichtbar ({s})", s == 200 and "/nc/" not in html and "foto.png" not in html)
    s, _, _ = call("GET", f"{path}/nc/{ids['foto']}?a={A}&w=640", uid=None, api=False, raw=True)
    ok(f"Veroeffentlicht ohne Schalter: Datei nicht abrufbar ({s})", s == 404)
    bl = json.loads(json.dumps(a_blocks))
    bl[1]["data"]["public"] = True
    bl[1]["data"].pop("sig")
    s, d = save(P, json.dumps({"blocks": bl}))
    ok(f"A: Schalter 'auf veroeffentlichter Seite zeigen' fuer das Bild ({s})", s == 200 and ncdata(P)[0]["public"] is True)
    s, html, _ = call("GET", path, uid=None, api=False, raw=True)
    html = html.decode()
    ok("Veroeffentlicht mit Schalter: Bild ueber die Seitenadresse, nie die Nextcloud-Adresse",
       f"/nc/{ids['foto']}" in html and U1 not in html and f"/nc/{ids['bericht']}" not in html)
    s, body, h = call("GET", f"{path}/nc/{ids['foto']}?a={A}&w=640", uid=None, api=False, raw=True)
    ok(f"Besucher: Vorschau ({s})", s == 200 and body.startswith(b"\x89PNG"))
    s, _, _ = call("GET", f"{path}/nc/{ids['bericht']}?a={A}&dl=1", uid=None, api=False, raw=True)
    ok(f"Besucher: Datei ohne Schalter weiter gesperrt ({s})", s == 404)
    call("DELETE", f"/api/notes/{P}/publish")
    s, _, _ = call("GET", f"{path}/nc/{ids['foto']}?a={A}&w=640", uid=None, api=False, raw=True)
    ok(f"Veroeffentlichung beendet: nichts mehr ({s})", s == 404)
    s, d = save(P, json.dumps({"blocks": a_blocks}))


def part_admin_disconnect(P, ids):
    setting("nc_files", False)
    s, _, _ = call("GET", f"/api/notes/{P}/nc/{ids['foto']}?a={A}&w=640", uid=C, api=False, raw=True)
    ok(f"Admin-Schalter aus: keine Vorschau ({s})", s == 404)
    s, d = call("GET", "/api/nextcloud/search")
    ok(f"Admin-Schalter aus: keine Auswahl ({s})", s == 403)
    s, d = call("GET", f"/api/notes/{P}")
    s2, d2 = save(P, d["body"])
    ok(f"Admin-Schalter aus: Seite mit bestehenden Bloecken speichern geht weiter ({s2})", s2 == 200 and len(ncdata(P)) == 4)
    setting("nc_files", True)
    s, _, _ = call("GET", f"/api/notes/{P}/nc/{ids['foto']}?a={A}&w=640", uid=C, api=False, raw=True)
    ok(f"Admin-Schalter wieder an: Vorschau ({s})", s == 200)
    rev0 = stats(U1).get("revoked", 0)
    s, d = call("DELETE", "/api/settings/nextcloud")
    ok(f"A: Trennen widerruft das App-Passwort ({s})", s == 200 and d.get("revoked") is True and stats(U1)["revoked"] == rev0 + 1
       and not q("SELECT 1 FROM nc_account WHERE uid=?", (A,)))
    s, _, _ = call("GET", f"/api/notes/{P}/nc/{ids['foto']}?a={A}&w=640", uid=C, api=False, raw=True)
    s2, d2 = call("GET", f"/api/notes/{P}/nc", uid=C)
    ok(f"Nach dem Trennen: Karte ohne Inhalt ({s}, Status ok=False)", s == 404 and s2 == 200 and not any(x["ok"] for x in d2["items"]))
    ok("Nach dem Trennen: Vorschauen des Kontos geloescht", not os.listdir(os.path.join(STATE, "nc-cache")))
    s, d = call("GET", f"/api/notes/{P}")
    s2, d2 = save(P, d["body"] + " ")
    s2, d2 = save(P, json.dumps(json.loads(d["body"])))
    ok(f"Nach dem Trennen: Seite mit eigenen Bloecken speichern geht ({s2})", s2 == 200)
    s, d = save(P, json.dumps({"blocks": json.loads(d["body"])["blocks"] + [ncb(ids["notiz"], U1, name="notiz.txt")]}))
    ok(f"Nach dem Trennen: neue Datei einfuegen -> 409 ({s})", s == 409)
    # Datei weg -> 404
    connect(A, U1)
    s, _, _ = call("GET", f"/api/notes/{P}/nc/{ids['foto']}?a={A}&w=640", uid=C, api=False, raw=True)
    ok(f"Neu verbunden: wieder Inhalt ({s})", s == 200)
    # Kontoloeschung: App-Passwort widerrufen, Zeile weg (eigenes Wegwerf-Konto E an NC2)
    from saga import userdata
    E = "hk-test-nce" + TAG
    auth.create_user(E, None, E)
    s, st = connect(E, U2)
    rev0 = stats(U2).get("revoked", 0)
    userdata.delete_user_data(E, drop_user=True)
    ok(f"Kontoloeschung: App-Passwort widerrufen, Verknuepfung weg ({s})",
       s == 200 and stats(U2)["revoked"] == rev0 + 1 and not q("SELECT 1 FROM nc_account WHERE uid=?", (E,)))


def part_unit():
    """Signatur/Pruefung ohne Server."""
    from saga import nextcloud as ncm
    d = {"author": "x", "inst": "https://n.example", "fileid": "5", "public": False, "fed": False}
    s = ncm.sign(7, d)
    ok("Signatur haengt an Seite und Schaltern", ncm.sig_ok(7, dict(d, sig=s)) and not ncm.sig_ok(8, dict(d, sig=s))
       and not ncm.sig_ok(7, dict(d, sig=s, public=True)) and not ncm.sig_ok(7, dict(d, sig=s, author="y")))
    ok("Platzhalter fuer andere Instanzen verraet den Namen nicht", ncm.pseudo("x").startswith("~") and "x" not in ncm.pseudo("x")[1:3])
    ok("Markdown: Link", ncm.block_md({"name": "a&amp;b.pdf", "inst": "https://n.example", "fileid": "5"}) == "[📄 a&b.pdf](https://n.example/f/5)")


# =====================================================================================================
# Foederation: zwei eigene Instanzen
# =====================================================================================================
def part_fed():
    import foederation as T
    import foederation2 as F2
    T.I["a"].update(port=8495, admin=8494, db="saga_nca", url="http://127.0.0.1:8495", host="127.0.0.1:8495", state=os.path.join(STATE, "ncfed-a"),
                    allow="localhost:8497")
    T.I["b"].update(port=8497, admin=8496, db="saga_ncb", url="http://localhost:8497", host="localhost:8497", state=os.path.join(STATE, "ncfed-b"),
                    allow="127.0.0.1:8495")
    T.res = res
    T.ok = ok
    F2.ok = ok
    P3 = 18343
    NC3 = fakedav.start(P3, True)
    U3 = f"http://127.0.0.1:{P3}"
    try:
        Ai, Bi, pa, pb = F2.main()
        T.py("a", f"import auth\nauth.save_setting('allowed_hosts', 'localhost:8497, 127.0.0.1:{P3}')\nprint('true')")
        time.sleep(5.5)
        an, ber = "fa-anna", "fb-bert"
        s, d = T.api("a", an, "POST", "/api/settings/nextcloud", {"url": U3})
        urllib.request.urlopen(U3 + "/_grant", timeout=10).read()
        ok("Foederation: Anna (A) verbindet ihre Nextcloud", T.wait(lambda: T.api("a", an, "GET", "/api/settings/nextcloud")[1].get("connected"), 20))
        fid = put(U3, "Fotos/bild.png", PNG)
        fid2 = put(U3, "doc.txt", b"Text fuer Bert")
        s, d = T.api("a", an, "POST", "/api/notes", {"title": "Mit Nextcloud"})
        Pf = d["id"]
        body = doc(para("x"), ncb(fid, U3, kind="image", name="bild.png", mime="image/png"), ncb(fid2, U3, name="doc.txt", mime="text/plain"))
        s, d = T.api("a", an, "PATCH", f"/api/notes/{Pf}", {"body": body, "format": "blocks"})
        ok(f"Foederation: Seite mit zwei Nextcloud-Bloecken ({s})", s == 200)
        s, d = T.api("a", an, "PUT", f"/api/notes/{Pf}/shares", {"type": "remote", "target": f"@bert@{T.I['b']['host']}", "role": "edit"})
        ok(f"Foederation: mit Bert (B) geteilt ({s})", s == 200)
        ok("Foederation: Faehigkeit nc_files ausgehandelt",
           T.wait(lambda: "nc_files" in json.loads(T.sql("a", "SELECT caps FROM fed_peer")[0][0] or "[]"), 20))
        r = -(Pf * 1024 + pb)
        s, d = T.api("b", ber, "GET", f"/api/notes/{r}")
        bd = d.get("body") or "" if isinstance(d, dict) else ""
        ok(f"B: Seite gelesen, Block da, Autor nur als Platzhalter ({s})", s == 200 and '"nextcloud"' in bd and an not in bd)
        auth_ps = [b["data"]["author"] for b in json.loads(bd)["blocks"] if b["type"] == "nextcloud"] if bd else []
        s, d = T.api("b", ber, "GET", f"/api/notes/{r}/nc")
        ok(f"B: ohne Schalter 'verbundene Server': Status ohne Inhalt ({s})", s == 200 and d["items"] and not any(x["ok"] for x in d["items"]))
        s, h, data = T.hreq("b", "GET", f"/api/notes/{r}/nc/{fid}?a={urllib.parse.quote(auth_ps[0] if auth_ps else '')}&w=320", cookie=T.ck("b", ber))
        ok(f"B: ohne Schalter keine Vorschau ({s})", s == 404)
        # Bert (Bearbeiter) speichert die Seite unveraendert -> A's Bloecke bleiben (echter Autor, gueltige Signatur)
        bl = json.loads(bd)["blocks"]
        s, d = T.api("b", ber, "PATCH", f"/api/notes/{r}", {"body": json.dumps({"blocks": bl + [para("von Bert")]}), "format": "blocks"})
        rows = T.sql("a", "SELECT body FROM note WHERE id=%s" % int(Pf))
        stored = [b["data"] for b in json.loads(rows[0][0])["blocks"] if b["type"] == "nextcloud"]
        ok(f"B: speichert unveraendert, A's Bloecke bleiben mit echtem Autor ({s})", s == 200 and all(x["author"] == an for x in stored) and len(stored) == 2)
        s, d = T.api("b", ber, "PATCH", f"/api/notes/{r}", {"body": json.dumps({"blocks": bl + [ncb(fid2, U3, name="neu")]}), "format": "blocks"})
        ok(f"B: neuer Nextcloud-Block ueber fremde Instanz abgewiesen ({s})", s in (403, 400))
        sw = json.loads(json.dumps(bl))
        sw[1]["data"]["fed"] = True
        s, d = T.api("b", ber, "PATCH", f"/api/notes/{r}", {"body": json.dumps({"blocks": sw}), "format": "blocks"})
        ok(f"B: Schalter fremder Bloecke abgewiesen ({s})", s == 403)
        # Anna schaltet "ueber verbundene Server zeigen" fuer das Bild ein
        own = json.loads(rows[0][0])["blocks"]
        for b in own:
            if b["type"] == "nextcloud" and b["data"]["fileid"] == str(fid):
                b["data"]["fed"] = True
        s, d = T.api("a", an, "PATCH", f"/api/notes/{Pf}", {"body": json.dumps({"blocks": own}), "format": "blocks"})
        ok(f"A: Schalter 'ueber verbundene Server zeigen' ({s})", s == 200)
        s, d = T.api("b", ber, "GET", f"/api/notes/{r}/nc")
        st = {x["fileid"]: x for x in d.get("items", [])}
        ok(f"B: Status mit Schalter ({s})", s == 200 and st.get(str(fid), {}).get("ok") and not st.get(str(fid2), {}).get("ok")
           and an not in json.dumps(d))
        s, h, data = T.hreq("b", "GET", f"/api/notes/{r}/nc/{fid}?a={urllib.parse.quote(auth_ps[0])}&w=320", cookie=T.ck("b", ber))
        ok(f"B: Vorschau ueber den eigenen Server ({s})", s == 200 and data.startswith(b"\x89PNG"))
        s, h, data = T.hreq("b", "GET", f"/api/notes/{r}/nc/{fid}?a={urllib.parse.quote(auth_ps[0])}&dl=1", cookie=T.ck("b", ber))
        ok(f"B: Download ueber den eigenen Server ({s})", s == 200 and data == PNG and "attachment" in h.get("content-disposition", ""))
        s, h, data = T.hreq("b", "GET", f"/api/notes/{r}/nc/{fid2}?a={urllib.parse.quote(auth_ps[1])}&dl=1", cookie=T.ck("b", ber))
        ok(f"B: Datei ohne Schalter weiter gesperrt ({s})", s == 404)
        s, h, data = T.hreq("b", "GET", f"/api/notes/{r}/nc/{fid}?a={an}&w=320", cookie=T.ck("b", "fb-olga"))
        ok(f"B: andere Person ohne Freigabe: nichts ({s})", s in (403, 404))
    finally:
        T.stop_all()
        for k in T.I:
            n, txt = T.server_errors(k)
            ok(f"Foederation {k.upper()}: Serverfehler {n}", n == 0)
            if n:
                print(txt, flush=True)
            try:
                T.pg_admin(f"DROP DATABASE IF EXISTS {T.I[k]['db']} WITH (FORCE)")
            except Exception as ex:  # noqa: BLE001
                print("aufraeumen:", ex)
        NC3.shutdown()


def main():
    try:
        part_unit()
        part_connect()
        ids = part_pick()
        P = part_blocks(ids)
        part_read(P, ids)
        a_blocks = part_editor(P, ids)
        part_copy(P, ids)
        part_publish(P, ids, a_blocks)
        part_admin_disconnect(P, ids)
        if os.environ.get("NC_SKIP_FED") != "1":
            part_fed()
    except Exception:
        import traceback
        traceback.print_exc()
        res.append(False)
    finally:
        for u in (C, D):
            try:
                with db.connect() as c, c:
                    c.execute("DELETE FROM note_share WHERE target=?", (u,))
                    c.execute("DELETE FROM users WHERE username=?", (u,))
            except Exception:  # noqa: BLE001
                pass
        try:
            auth.save_setting("nc_files", True)
        except Exception:  # noqa: BLE001
            pass
    print(f"\nNEXTCLOUD-DATEIEN {sum(res)}/{len(res)} - " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
