"""Persoenliche Backups je Konto (2.3, saga/userbackup.py) gegen nachgemachte Ziele (tests/fakedav.py: Nextcloud mit Login
Flow v2 und schlichtes WebDAV) und die laufende Test-Instanz (HK_TEST_BASE).

Prueft: SSRF-Schutz (nur https/oeffentlich, interne Hosts nur mit Admin-Freigabe), Verbinden per Login Flow v2 (App-Passwort
verschluesselt, Trennen widerruft), Schluessel (erzeugen/bestaetigen, eigener oeffentlicher, privater abgelehnt), Lauf ->
Backup im Ziel (nichts im Klartext, Pruefsummen-Datei), Inhalt NUR eigener Daten (fremde Freigaben, fremde Dateien und
fremde Kommentare per Standard NICHT enthalten), Ausschluesse (Seitenbaum, Kategorien), Entschluesseln mit zwei Schluesseln,
Manifest-Pruefsummen, Markdown, Rechte (fremdes Konto kann nicht ausloesen/abrufen/einspielen), Einspielen in ein frisches
Konto (Seiten, Unterseiten, Datenbank-Zeilen + Relationen, Bilder, Verweise, Vorlagen, Versionen), Admin-Schalter,
Grenzen (Groesse -> Benachrichtigung, Haeufigkeit), Zeitplan, schlichtes WebDAV, Kontoloeschung."""
import base64
import hashlib
import io
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

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
FILES = os.path.join(STATE, "files")
A, BB, NEW = "hk-test-a", "hk-test-b", "hk-test-ub" + secrets.token_hex(3)
NCP, DVP = 18321, 18322
NC, DV = fakedav.start(NCP, True), fakedav.start(DVP, False)
NCB, DVB = f"http://127.0.0.1:{NCP}", f"http://127.0.0.1:{DVP}"
MARK = "UBK-" + secrets.token_hex(6)
TMP = tempfile.mkdtemp(prefix="ubk-", dir=STATE)
res = []


def ok(n, c, info=None):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, "" if c or info is None else f"-> {info}", flush=True)


def cookie(uid):
    return "saga=" + TimestampSigner(os.environ["SESSION_SECRET"]).sign(base64.b64encode(json.dumps({"uid": uid}).encode())).decode()


def call(m, p, b=None, uid=A, raw=None, ctype=None):
    h = {"Host": HOST, "Cookie": cookie(uid), "X-Saga": "1"}
    data = None
    if raw is not None:
        data, h["Content-Type"] = raw, ctype or "application/octet-stream"
    elif b is not None:
        data, h["Content-Type"] = json.dumps(b).encode(), "application/json"
    req = urllib.request.Request(B + p, data=data, method=m, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            body = r.read()
            try:
                return r.status, json.loads(body)
            except ValueError:
                return r.status, body
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, body


def q(sql, args=()):
    with db.connect() as c, c:
        cur = c.execute(sql, args)
        try:
            return cur.fetchall()
        except Exception:  # noqa: BLE001
            return None


def admin_set(**kw):
    cur = dict(auth.settings(fresh=True).get("user_backup") or {})
    cur.update(kw)
    auth.save_setting("user_backup", cur)
    time.sleep(5.5)                                  # Einstellungs-Cache des Servers (5 s)


def stats(base=NCB):
    return json.loads(urllib.request.urlopen(base + "/_stats", timeout=10).read())


def fake_get(path, base=NCB):
    with urllib.request.urlopen(base + "/_get?" + urllib.parse.urlencode({"f": path}), timeout=30) as r:
        return r.read()


def blocks(*bs):
    return json.dumps({"time": 1, "version": "2.30.7", "blocks": [{"id": f"b{i}", "type": t, "data": d} for i, (t, d) in enumerate(bs)]})


def note(uid, title, body="", parent=None, **kw):
    cols = {"uid": uid, "title": title, "body": body, "format": "blocks" if body else "text", "parent_id": parent,
            "created": time.time(), "updated": time.time(), "created_by": uid}
    cols.update(kw)
    with db.connect() as c, c:
        return c.execute(f"INSERT INTO note ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", tuple(cols.values())).lastrowid


def file_(uid, name, data, img=False):
    fid = os.urandom(12).hex()
    with open(os.path.join(FILES, fid), "wb") as f:
        f.write(data)
    q("INSERT INTO file (id, uid, name, mime, size, is_image, created) VALUES (?,?,?,?,?,?,?)",
      (fid, uid, name, "image/png" if img else "application/octet-stream", len(data), 1 if img else 0, time.time()))
    return fid


def wait_run(uid=A, t=60, since=0):
    t0 = time.time()
    end = t0 + t
    while time.time() < end:
        st, s = call("GET", "/api/account/backup", uid=uid)
        if s["runs"] and s["runs"][0]["finished"] and s["runs"][0]["started"] > since and not s["running"]:
            return s
        time.sleep(0.7)
    print("  (wait_run: Zeit abgelaufen nach", round(time.time() - t0), "s:", s["running"], s["runs"][:2], ")", flush=True)
    return s


def run_now(uid=A):
    prev = call("GET", "/api/account/backup", uid=uid)[1]["runs"]
    t0 = prev[0]["started"] if prev else 0                # neuer Lauf = juenger als der bisher letzte
    st, r = call("POST", "/api/account/backup/run", uid=uid)
    if st != 202:
        print("  (run_now:", st, r, ")", flush=True)
    return st, r, (wait_run(uid, since=t0) if st == 202 else None)


def age_dec(path, key):
    kf = os.path.join(TMP, "k-" + secrets.token_hex(4))
    with open(kf, "w") as f:
        f.write(key)
    out = path + ".zip"
    r = subprocess.run(["age", "-d", "-i", kf, "-o", out, path], capture_output=True)
    os.remove(kf)
    return out if r.returncode == 0 else None


def latest(base=NCB, root=f"/remote.php/dav/files/{fakedav.UID}/Saga-Backup"):
    names = sorted(k for k in stats(base)["files"] if k.startswith(root + "/") and k.endswith(".zip.age"))
    return names[-1] if names else None


def fetch_zip(key, uid=A, base=NCB, root=None):
    """Neuestes Backup ueber die API herunterladen (wie die Person) und entschluesseln -> (zip, roh-bytes, name)."""
    st, items = call("GET", "/api/account/backup/remote", uid=uid)
    item = next(x for x in items["items"] if x["complete"])
    st, raw = call("GET", "/api/account/backup/remote/" + item["name"], uid=uid)
    p = os.path.join(TMP, item["name"])
    with open(p, "wb") as f:
        f.write(raw)
    zp = age_dec(p, key)
    return (zipfile.ZipFile(zp) if zp else None), raw, item["name"], p


try:
    for u in (A, BB):
        auth.get_user(u) or auth.create_user(u, None, u)
    auth.create_user(NEW, None, "Frisches Konto")
    admin_set(enabled=True, max_mb=1024, min_h=0, max_schedule="daily", hosts="")
    # --- Inhalte: A (eigene), B (fremde, mit A geteilt) ---
    img = b"\x89PNG\r\n\x1a\n" + os.urandom(800) + MARK.encode()
    f1 = file_(A, "bild.png", img, True)
    f3 = file_(A, "nur-privat.bin", b"PRIVAT-DATEI-" + MARK.encode())
    fb = file_(BB, "fremd.png", b"\x89PNG\r\n\x1a\nFREMD-" + MARK.encode(), True)
    p2 = note(A, "Ziel " + MARK)
    p1 = note(A, "Projekt " + MARK, blocks(("paragraph", {"text": f'Siehe <a href="/notes#{p2}">Ziel</a> {MARK}'}),
                                            ("image", {"file": {"url": f"/files/{f1}"}, "caption": "Bild"})), icon=f"/files/{f1}")
    p1a = note(A, "Unterseite " + MARK, blocks(("paragraph", {"text": "Kind"})), parent=p1)
    db2 = note(A, "Kunden", kind="database", schema=json.dumps({"props": [{"id": "ort", "name": "Ort", "type": "text"}]}))
    k1 = note(A, "Kunde Eins", parent=db2, props=json.dumps({"ort": "Bremen"}))
    db1 = note(A, "Auftraege", kind="database", schema=json.dumps({"props": [
        {"id": "kunde", "name": "Kunde", "type": "relation", "target": db2},
        {"id": "st", "name": "Status", "type": "select", "options": [{"id": "o1", "name": "offen", "color": "red"}]}]}))
    r1 = note(A, "Auftrag 1", parent=db1, props=json.dumps({"kunde": [k1], "st": "o1"}))
    emb = note(A, "Uebersicht", blocks(("database", {"db": db1}), ("buttons", {"buttons": [{"label": "Neu", "action": "page", "target": p2}]})))
    tpl = note(A, "Vorlage " + MARK, blocks(("paragraph", {"text": "tpl"})), is_template=1)
    arc = note(A, "Archiviert " + MARK, archived=time.time())
    trash = note(A, "Geloescht " + MARK, deleted_at=time.time())
    priv = note(A, "Privat " + MARK, blocks(("attaches", {"file": {"url": f"/files/{f3}"}, "title": "x"})))
    priv_sub = note(A, "Privat-Kind " + MARK, parent=priv)
    q("INSERT INTO note_version (note_id, uid, title, body, format, created, author) VALUES (?,?,?,?,?,?,?)",
      (p1, A, "Projekt alt", blocks(("paragraph", {"text": "alte Fassung"})), "blocks", time.time() - 100, BB))
    pb = note(BB, "B-Geheim " + MARK, blocks(("image", {"file": {"url": f"/files/{fb}"}})))
    q("INSERT INTO note_share (note_id, owner_uid, target_type, target, role, created) VALUES (?,?,?,?,?,?)", (pb, BB, "user", A, "edit", time.time()))
    q("INSERT INTO note_share (note_id, owner_uid, target_type, target, role, created) VALUES (?,?,?,?,?,?)", (p1, A, "user", BB, "comment", time.time()))
    c_own = q("INSERT INTO note_comment (note_id, owner_uid, uid, body, created) VALUES (?,?,?,?,?) RETURNING id",
              (p1, A, A, "EIGENER-KOMMENTAR", time.time()))[0][0]
    q("INSERT INTO note_comment (note_id, owner_uid, uid, body, created, thread_id) VALUES (?,?,?,?,?,?)",
      (p1, A, BB, "FREMDER-KOMMENTAR", time.time(), c_own))
    q("INSERT INTO note_comment (note_id, owner_uid, uid, body, created) VALUES (?,?,?,?,?)", (pb, BB, A, "A-AUF-FREMDER-SEITE", time.time()))
    q("INSERT INTO task (uid, text, created) VALUES (?,?,?)", (A, "Aufgabe " + MARK, time.time()))
    q("INSERT INTO bookmark (uid, name, url, created) VALUES (?,?,?,?)", (A, "Lesezeichen", "https://example.org/" + MARK, time.time()))

    # --- Status, SSRF ---
    st, s = call("GET", "/api/account/backup")
    ok(f"Status: erlaubt, kein Ziel, nicht bereit ({st})", st == 200 and s["allowed"] and s["target"] is None and not s["ready"])
    for bad, why in [(NCB, "http ohne Freigabe"), (f"https://127.0.0.1:{NCP}", "interne IP"), ("https://localhost:9", "localhost"),
                     ("https://169.254.169.254", "Metadaten-Adresse"), ("https://10.1.2.3", "privates Netz"), ("ftp://x.example", "Schema"),
                     ("https://u:p@cloud.example.org", "Zugangsdaten in der Adresse")]:
        st, r = call("POST", "/api/account/backup/connect", {"url": bad})
        ok(f"SSRF abgelehnt: {why} ({st})", st == 400, r)
    ok("Fake-Nextcloud wurde nie angefragt", not stats().get("paths"), stats().get("paths"))
    admin_set(hosts=f"127.0.0.1:{NCP}, 127.0.0.1:{DVP}")

    # --- Verbinden per Login Flow v2 ---
    st, r = call("POST", "/api/account/backup/connect", {"url": NCB, "folder": "Saga-Backup"})
    ok(f"Nextcloud erkannt -> Login Flow ({st})", st == 200 and str(r.get("flow", "")).startswith(NCB + "/login/v2/flow/"), r)
    st, s = call("GET", "/api/account/backup")
    ok("Status: Anmeldung laeuft (Link)", s["flow"]["state"] == "wait" and s["flow"]["login"])
    urllib.request.urlopen(NCB + "/_grant", timeout=10).read()
    for _ in range(30):
        st, s = call("GET", "/api/account/backup")
        if s["target"]:
            break
        time.sleep(0.5)
    ok(f"Verbunden: Nextcloud, App-Passwort ({s['target']})", s["target"] and s["target"]["kind"] == "nextcloud" and s["target"]["flow"])
    row = q("SELECT cfg, secret FROM user_backup WHERE uid=?", (A,))[0]
    pw = fakedav.LOGIN in row[0] and [v for k, v in NC.store.pw.items() if k == fakedav.LOGIN]
    ok("App-Passwort nur verschluesselt gespeichert", pw and pw[0] not in row[0] and pw[0] not in row[1] and len(row[1]) > 40)
    st, r = call("POST", "/api/account/backup/test")
    ok(f"Verbindung pruefen ({st})", st == 200 and r.get("ok"))
    st, r = call("POST", "/api/account/backup/run")
    ok(f"Ohne Schluessel: kein Lauf ({st})", st == 409)

    # --- Schluessel ---
    st, g = call("POST", "/api/account/backup/key/generate")
    ok("Schluesselpaar erzeugt (privat nur in der Antwort)", st == 200 and "AGE-SECRET-KEY-1" in g["private"] and g["public"].startswith("age1"))
    priv1 = g["private"]
    sk = priv1.strip().splitlines()[-1]
    ok("Privater Schluessel nirgends in der Datenbank", not q("SELECT 1 FROM user_backup WHERE cfg LIKE ? OR secret LIKE ? OR state LIKE ?",
                                                             (f"%{sk}%",) * 3))
    st, r = call("POST", "/api/account/backup/key/confirm", {"last6": "XXXXXX"})
    ok(f"Bestaetigung mit falschen Zeichen abgelehnt ({st})", st == 400)
    st, r = call("POST", "/api/account/backup/key/confirm", {"last6": sk[-6:].lower()})
    ok(f"Bestaetigt -> eingetragen ({st})", st == 200 and r.get("fp"))
    kp = subprocess.run(["age-keygen"], capture_output=True, text=True).stdout
    pub2 = next(x.split(": ")[1] for x in kp.splitlines() if x.startswith("# public key"))
    st, r = call("POST", "/api/account/backup/key", {"key": sk})
    ok(f"Privater Schluessel als oeffentlicher abgelehnt ({st})", st == 400)
    st, r = call("POST", "/api/account/backup/key", {"key": pub2})
    ok(f"Eigener oeffentlicher Schluessel eingetragen ({st})", st == 200)
    st, s = call("GET", "/api/account/backup?pages=1")
    ok("Zwei Schluessel, bereit, Seitenliste ohne fremde Seiten", len(s["keys"]) == 2 and s["ready"]
       and {p1, priv}.issubset({p["id"] for p in s["pages"]}) and pb not in {p["id"] for p in s["pages"]}
       and r1 not in {p["id"] for p in s["pages"]})

    # --- Ausschluss + Lauf ---
    st, r = call("PUT", "/api/account/backup/settings", {"exclude": [priv, pb, 999999999]})
    ok(f"Ausschluss gespeichert, fremde/unbekannte IDs verworfen ({r.get('exclude') if isinstance(r, dict) else r})", st == 200 and r["exclude"] == [priv])
    st, r, s = run_now()
    ok(f"Jetzt sichern -> fertig ({st}, {s and s['runs'][0]})", st == 202 and s and s["runs"][0]["ok"] and s["last_ok"])
    name = latest()
    raw_remote = fake_get(name)
    ok("Im Ziel: .zip.age (age), nichts im Klartext", raw_remote.startswith(b"age-encryption.org/v1") and MARK.encode() not in raw_remote)
    side = fake_get(name + ".sha256").decode()
    ok("Pruefsummen-Datei (sha256sum-Format) passt", side.split()[0] == hashlib.sha256(raw_remote).hexdigest() and side.split()[1] == name.rsplit("/", 1)[1])
    z, raw, bname, bpath = fetch_zip(priv1)
    ok("Download ueber die API == Datei im Ziel; mit Schluessel 1 entschluesselt", raw == raw_remote and z is not None)
    ok("Auch mit Schluessel 2 lesbar", age_dec(bpath, kp) is not None)
    names = set(z.namelist())
    man = json.loads(z.read("manifest.json"))
    good = all(hashlib.sha256(z.read(n)).hexdigest() == v["sha256"] and len(z.read(n)) == v["size"] for n, v in man["files"].items())
    ok(f"Manifest: Pruefsummen aller Dateien stimmen ({len(man['files'])})", good and set(man["files"]) | {"manifest.json"} == names)
    data = json.loads(z.read("daten.json"))
    titles = {n["title"] for n in data["notes"]}
    ids = {n["id"] for n in data["notes"]}
    ok("Eigene Seiten, Unterseiten, Datenbanken + Zeilen enthalten", {p1, p1a, p2, db1, db2, r1, k1, emb}.issubset(ids))
    ok("Vorlage, Archiv, Papierkorb enthalten (Standard an)", {tpl, arc, trash}.issubset(ids))
    ok("Ausgeschlossener Seitenbaum fehlt (mit Unterseite)", priv not in ids and priv_sub not in ids)
    ok("FREMDE Seite (mit mir geteilt) fehlt", pb not in ids and ("B-Geheim " + MARK) not in titles)
    fids = {f["id"] for f in data["files"]}
    ok("Eigenes Bild enthalten, FREMDE Datei fehlt, Datei nur des ausgeschlossenen Baums fehlt", f1 in fids and fb not in fids and f3 not in fids)
    blob = b"".join(z.read(n) for n in names)
    ok("Kein Inhalt anderer Konten im Backup (fremde Datei, fremde Seite)", b"FREMD-" not in blob and b"B-Geheim" not in blob)
    cms = [c["body"] for c in data["comments"]]
    ok(f"Kommentare: eigener drin, FREMDER per Standard nicht, eigener auf fremder Seite nicht ({cms})",
       cms == ["EIGENER-KOMMENTAR"] and b"FREMDER-KOMMENTAR" not in blob and b"A-AUF-FREMDER-SEITE" not in blob)
    ok("Kein fremder Benutzername im Backup (Versions-Autor weggelassen)", BB.encode() not in blob)
    ok("Versionen, Aufgaben, Lesezeichen, Profil enthalten", len(data["versions"]) == 1 and data["tasks"] and data["bookmarks"]
       and data["account"]["username"] == A)
    md = [n for n in names if n.startswith("seiten/") and n.endswith(".md")]
    pmd = f"seiten/Projekt {MARK}.md" if f"seiten/Projekt {MARK}.md" in md else ""
    txt = z.read(pmd).decode() if pmd else ""
    ok(f"Markdown: Seitenbaum, Verweis und Bild relativ ({pmd})", pmd and "Ziel" in txt and "../dateien/" in txt
       and any(n.startswith(pmd[:-3] + "/Unterseite") for n in md))
    ok("Vorlage/Archiv/Papierkorb als eigene Ordner, LIESMICH.txt", any(n.startswith("vorlagen/") for n in names)
       and any(n.startswith("archiv/") for n in names) and any(n.startswith("papierkorb/") for n in names) and "LIESMICH.txt" in names)
    ok("Bild-Datei im ZIP unveraendert", z.read(next(f["path"] for f in data["files"] if f["id"] == f1)) == img)

    # --- Rechte: fremdes Konto ---
    st, sb = call("GET", "/api/account/backup", uid=BB)
    ok("B sieht weder Ziel noch Laeufe von A", st == 200 and sb["target"] is None and not sb["runs"] and not sb["keys"])
    st, r = call("GET", "/api/account/backup/remote/" + bname, uid=BB)
    ok(f"B kann A's Backup nicht abrufen ({st})", st == 404)
    st, r = call("GET", "/api/account/backup/remote", uid=BB)
    ok("B: Liste im Ziel leer", st == 200 and r["items"] == [])
    st, r = call("POST", "/api/account/backup/run", uid=BB)
    ok(f"B kann fuer sich ohne Ziel nichts ausloesen ({st})", st == 409)
    st, up = call("POST", "/api/account/backup/restore/upload", raw=raw)
    ok(f"Upload als A -> Token ({st})", st == 200 and up.get("encrypted"))
    st, r = call("POST", "/api/account/backup/restore", {"token": up["token"], "key": priv1}, uid=BB)
    ok(f"B kann A's Upload nicht einspielen ({st})", st == 404)
    st, r = call("GET", "/api/account/backup", uid="")
    ok(f"Ohne Anmeldung: 401 ({st})", st == 401)

    # --- Kommentare anderer: einschaltbar; Kategorien aus ---
    st, r = call("PUT", "/api/account/backup/settings", {"cats": {"comments_others": True, "trash": False, "versions": False, "templates": False,
                                                                  "bookmarks": False}})
    ok(f"Kategorien gespeichert ({st})", st == 200 and r["cats"]["comments_others"] and not r["cats"]["trash"], r)
    st, r, s = run_now()
    z2, *_ = fetch_zip(priv1)
    d2 = json.loads(z2.read("daten.json"))
    ids2 = {n["id"] for n in d2["notes"]}
    ok("Kommentare anderer: eingeschaltet -> enthalten (mit Autor)", any(c["body"] == "FREMDER-KOMMENTAR" and c["author"] == BB for c in d2["comments"]))
    ok("Kategorien aus: Papierkorb, Vorlagen, Versionen, Lesezeichen fehlen", trash not in ids2 and tpl not in ids2 and not d2["versions"]
       and not d2["bookmarks"] and p1 in ids2)
    call("PUT", "/api/account/backup/settings", {"cats": {"comments_others": False, "trash": True, "versions": True, "templates": True, "bookmarks": True}})
    st, sa = call("GET", "/api/account/backup")
    ok(f"Aufbewahrung: hoechstens {sa['keep']} Backups im Ziel", len([k for k in stats()["files"] if k.endswith(".zip.age")]) <= sa["keep"])

    # --- Einspielen in ein frisches Konto ---
    st, up = call("POST", "/api/account/backup/restore/upload", raw=raw, uid=NEW)
    wrong = subprocess.run(["age-keygen"], capture_output=True, text=True).stdout
    st, r = call("POST", "/api/account/backup/restore", {"token": up["token"], "key": wrong}, uid=NEW)
    ok(f"Falscher Schluessel -> Meldung ({st})", st == 400)
    st, r = call("POST", "/api/account/backup/restore", {"token": up["token"], "key": priv1, "trash": True, "tasks": True}, uid=NEW)
    ok(f"Eingespielt ins frische Konto ({r})", st == 200 and r["pages"] == len(data["notes"]) and r["files"] == 1 and r["folder"])
    got = {t: (i, par, body, icon, kind, schema, props, tmpl, dele)
           for i, t, par, body, icon, kind, schema, props, tmpl, dele in
           q("SELECT id, title, parent_id, body, icon, kind, schema, props, is_template, deleted_at FROM note WHERE uid=?", (NEW,))}
    n1, n2, n1a = got["Projekt " + MARK], got["Ziel " + MARK], got["Unterseite " + MARK]
    newf = q("SELECT id, name FROM file WHERE uid=?", (NEW,))
    nf = newf[0][0] if newf else ""
    ok("Neue IDs; Seite unter \"Wiederhergestellt\"", n1[0] != p1 and n1[1] == r["folder"])
    ok("Unterseite unter der neuen Seite", n1a[1] == n1[0])
    ok("Verweis auf neue Seite umgeschrieben", f'/notes#{n2[0]}' in n1[2] and f'/notes#{p2}"' not in n1[2])
    ok("Bild: neue Datei des neuen Kontos, Inhalt gleich, Verweis + Symbol umgeschrieben",
       nf and nf != f1 and f"/files/{nf}" in n1[2] and n1[3] == f"/files/{nf}" and open(os.path.join(FILES, nf), "rb").read() == img)
    nd1, nd2 = got["Auftraege"], got["Kunden"]
    nr1, nk1 = got["Auftrag 1"], got["Kunde Eins"]
    sch = json.loads(nd1[5])
    ok("Datenbank-Zeilen unter der neuen Datenbank", nr1[1] == nd1[0] and nk1[1] == nd2[0])
    ok("Relation: Ziel-Datenbank und Werte umgeschrieben", sch["props"][0]["target"] == nd2[0] and json.loads(nr1[6])["kunde"] == [nk1[0]]
       and json.loads(nr1[6])["st"] == "o1")
    eb = json.loads(got["Uebersicht"][2])["blocks"]
    ok("Eingebettete Datenbank + Schaltflaeche zeigen auf neue Seiten", eb[0]["data"]["db"] == nd1[0] and eb[1]["data"]["buttons"][0]["target"] == n2[0])
    ok("Vorlage bleibt Vorlage, Papierkorb im Papierkorb", got["Vorlage " + MARK][7] == 1 and got["Geloescht " + MARK][8])
    ok("Versionen und Aufgaben uebernommen", q("SELECT COUNT(*) FROM note_version WHERE uid=? AND note_id=?", (NEW, n1[0]))[0][0] == 1
       and q("SELECT COUNT(*) FROM task WHERE uid=?", (NEW,))[0][0] == 1)
    ok("A's Daten unveraendert (nichts verschoben)", q("SELECT uid FROM note WHERE id=?", (p1,))[0][0] == A
       and q("SELECT COUNT(*) FROM file WHERE uid=?", (A,))[0][0] >= 2)
    st, r = call("GET", f"/api/notes/{n1[0]}", uid=NEW)
    ok(f"Neue Seite ueber die API lesbar ({st})", st == 200)
    st, r = call("GET", f"/files/{nf}", uid=NEW)
    ok(f"Neue Datei ueber /files lesbar ({st})", st == 200 and r == img)
    # entschluesseltes ZIP direkt (ohne Schluessel), ohne Ordner
    with open(z.filename, "rb") as f:
        st, up = call("POST", "/api/account/backup/restore/upload", raw=f.read(), uid=NEW)
    st, r = call("POST", "/api/account/backup/restore", {"token": up["token"], "folder": False}, uid=NEW)
    ok(f"Entschluesseltes .zip ohne Schluessel, ohne Ordner ({st})", st == 200 and r["pages"] == len(data["notes"]) - 1 and not r["folder"])
    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w") as zz:
        zz.writestr("manifest.json", json.dumps({"format": "saga-userbackup/1", "files": {"daten.json": {"size": 1, "sha256": "0" * 64}}}))
        zz.writestr("daten.json", "{}")
    st, up = call("POST", "/api/account/backup/restore/upload", raw=bad.getvalue(), uid=NEW)
    st, r = call("POST", "/api/account/backup/restore", {"token": up["token"]}, uid=NEW)
    ok(f"Manipuliertes Backup (Pruefsumme) abgelehnt ({st})", st == 400)
    xss = io.BytesIO()
    evil = {"format": "saga-userbackup/1", "notes": [{"id": 1, "title": "X", "format": "blocks",
                                                       "body": blocks(("paragraph", {"text": '<img src=x onerror=alert(1)>hallo<script>x</script>'}))}]}
    ev = json.dumps(evil).encode()
    with zipfile.ZipFile(xss, "w") as zz:
        zz.writestr("daten.json", ev)
        zz.writestr("manifest.json", json.dumps({"format": "saga-userbackup/1", "files": {"daten.json": {"size": len(ev), "sha256": hashlib.sha256(ev).hexdigest()}}}))
    st, up = call("POST", "/api/account/backup/restore/upload", raw=xss.getvalue(), uid=NEW)
    st, r = call("POST", "/api/account/backup/restore", {"token": up["token"], "folder": False}, uid=NEW)
    xb = q("SELECT body FROM note WHERE uid=? AND title='X'", (NEW,))
    ok(f"Eingespielte Inhalte werden bereinigt (kein onerror/script) ({st})", st == 200 and xb and "onerror" not in xb[0][0] and "<script" not in xb[0][0])
    # Bild-/Datei-Bloecke nur mit eigenen Dateien (wie beim Speichern): fremde Datei, fremde Adresse -> Absatz mit Namen
    evil = {"format": "saga-userbackup/1", "notes": [{"id": 1, "title": "Y", "format": "blocks", "body": blocks(
        ("image", {"file": {"url": "https://tracker.example/px.png"}, "caption": "Pixel"}),
        ("columns", {"cols": [{"blocks": [{"id": "i1", "type": "attaches", "data": {"file": {"url": f"/files/{fb}", "name": "fremd.png"}}}]}]}))}],
            "versions": [{"note_id": 1, "title": "Y", "format": "blocks", "created": 1,
                          "body": blocks(("image", {"file": {"url": f"/files/{fb}"}, "caption": "alt"}))}]}
    ev = json.dumps(evil).encode()
    yz = io.BytesIO()
    with zipfile.ZipFile(yz, "w") as zz:
        zz.writestr("daten.json", ev)
        zz.writestr("manifest.json", json.dumps({"format": "saga-userbackup/1", "files": {"daten.json": {"size": len(ev), "sha256": hashlib.sha256(ev).hexdigest()}}}))
    st, up1 = call("POST", "/api/account/backup/restore/upload", raw=yz.getvalue(), uid=NEW)
    st, up = call("POST", "/api/account/backup/restore/upload", raw=yz.getvalue(), uid=NEW)
    st0, r0 = call("POST", "/api/account/backup/restore", {"token": up1["token"], "folder": False}, uid=NEW)
    ok(f"Neuer Upload ersetzt den wartenden (hoechstens einer je Konto) ({st0})", st0 == 404)
    st, r = call("POST", "/api/account/backup/restore", {"token": up["token"], "folder": False}, uid=NEW)
    yb = q("SELECT id, body FROM note WHERE uid=? AND title='Y'", (NEW,))
    yv = q("SELECT body FROM note_version WHERE uid=? AND note_id=?", (NEW, yb[0][0])) if yb else []
    ok(f"Fremde Datei/Adresse in Bild-/Datei-Bloecken entfernt (Seite + Version) ({st})", st == 200 and yb and yv
       and "tracker.example" not in yb[0][1] and fb not in yb[0][1] and fb not in yv[0][0] and '"image"' not in yb[0][1]
       and "attaches" not in yb[0][1] and "fremd.png" in yb[0][1])

    # --- Grenzen beim Einspielen (A3): daten.json, Dateizahl, Versionen je Seite, Textseiten; Upload danach sofort weg ---
    def mkzip(data, extra=(), sums_extra=None, raw_data=None):
        ev = raw_data if raw_data is not None else json.dumps(data).encode()
        bz = io.BytesIO()
        sums = {"daten.json": {"size": len(ev), "sha256": hashlib.sha256(ev).hexdigest()}}
        sums.update(sums_extra or {})
        with zipfile.ZipFile(bz, "w", zipfile.ZIP_DEFLATED) as zz:
            zz.writestr("daten.json", ev)
            for n_, d_ in extra:
                zz.writestr(n_, d_)
            zz.writestr("manifest.json", json.dumps({"format": "saga-userbackup/1", "files": sums}))
        return bz.getvalue()

    def up_path(tok):
        return os.path.join(db.BACKUP_DIR, "user-tmp", "uploads", tok)
    st, up = call("POST", "/api/account/backup/restore/upload", raw=bad.getvalue(), uid=NEW)
    there = os.path.isfile(up_path(up["token"]))
    st, r = call("POST", "/api/account/backup/restore", {"token": up["token"]}, uid=NEW)
    st2, r2 = call("POST", "/api/account/backup/restore", {"token": up["token"]}, uid=NEW)
    ok(f"Fehlgeschlagenes Einspielen: hochgeladenes ZIP sofort geloescht, Token verbraucht ({st} {st2})",
       there and st == 400 and st2 == 404 and not os.path.exists(up_path(up["token"])))
    long_txt = "L" * 50000
    vers = [{"note_id": 1, "title": f"V{i}", "format": "text", "body": f"v{i}", "created": 1000 + i} for i in range(60)]
    st, up = call("POST", "/api/account/backup/restore/upload", raw=mkzip({"format": "saga-userbackup/1", "versions": vers, "notes": [
        {"id": 1, "title": "Textseite " + MARK, "format": "text", "body": long_txt}]}), uid=NEW)
    st, r = call("POST", "/api/account/backup/restore", {"token": up["token"], "folder": False}, uid=NEW)
    tn = q("SELECT id, body FROM note WHERE uid=? AND title=?", (NEW, "Textseite " + MARK))
    tv = q("SELECT title FROM note_version WHERE uid=? AND note_id=? ORDER BY created", (NEW, tn[0][0])) if tn else []
    ok(f"Textseite auf NOTE_BODY_MAX gekuerzt ({len(tn[0][1]) if tn else '-'}), je Seite hoechstens 50 Versionen, die neuesten ({len(tv)})",
       st == 200 and tn and len(tn[0][1]) == 20000 and len(tv) == 50 and tv[0][0] == "V10" and tv[-1][0] == "V59"
       and r["versions"] == 50 and not os.path.exists(up_path(up["token"])))
    big_json = b'{"format": "saga-userbackup/1", "notes": []' + b" " * (64 * 1024 * 1024) + b"}"
    st, up = call("POST", "/api/account/backup/restore/upload", raw=mkzip(None, raw_data=big_json), uid=NEW)
    del big_json
    st, r = call("POST", "/api/account/backup/restore", {"token": up["token"]}, uid=NEW)
    ok(f"daten.json ueber 64 MB abgelehnt ({st} {r})", st == 400 and not os.path.exists(up_path(up["token"])))
    nfiles = 20001
    fl = [{"id": "%024x" % i, "name": f"f{i}", "path": f"dateien/{i}"} for i in range(nfiles)]
    st, up = call("POST", "/api/account/backup/restore/upload", raw=mkzip(
        {"format": "saga-userbackup/1", "notes": [{"id": 1, "title": "Viele " + MARK}], "files": fl},
        extra=[(f["path"], b"x") for f in fl],
        sums_extra={f["path"]: {"size": 1, "sha256": hashlib.sha256(b"x").hexdigest()} for f in fl}), uid=NEW)
    nf0 = q("SELECT COUNT(*) FROM file WHERE uid=?", (NEW,))[0][0]
    st, r = call("POST", "/api/account/backup/restore", {"token": up["token"]}, uid=NEW)
    ok(f"Mehr als 20000 Dateien abgelehnt, bevor etwas geschrieben wird ({st} {r})", st == 400 and "20000" in str(r)
       and q("SELECT COUNT(*) FROM file WHERE uid=?", (NEW,))[0][0] == nf0 and not q("SELECT 1 FROM note WHERE uid=? AND title=?", (NEW, "Viele " + MARK)))

    # --- Grenzen ---
    admin_set(min_h=1)
    st, r = call("POST", "/api/account/backup/run")
    ok(f"Haeufigkeit: zu frueh -> 429 ({st})", st == 429 and r.get("retry_at"))
    big = file_(A, "gross.bin", os.urandom(3 * 1024 * 1024))
    admin_set(min_h=0, max_mb=1)
    n0 = q("SELECT COUNT(*) FROM notification WHERE uid=? AND kind='userbackup.failed'", (A,))[0][0]
    st, r, s = run_now()
    ok(f"Groesse: zu gross -> Fehler mit Meldung ({s and s['runs'][0]['error']})", s and not s["runs"][0]["ok"] and "MB" in s["runs"][0]["error"])
    ok("Fehler -> Benachrichtigung an die Person", q("SELECT COUNT(*) FROM notification WHERE uid=? AND kind='userbackup.failed'", (A,))[0][0] == n0 + 1)
    st, nl = call("GET", "/api/notifications")
    items = nl.get("items", nl) if isinstance(nl, dict) else nl
    it = next((x for x in items if x.get("kind") == "userbackup.failed"), None) if isinstance(items, list) else None
    ok(f"Benachrichtigung lesbar mit Link zu den Einstellungen ({it and it.get('url')})", it and it.get("url") == "/?settings=backup" and it.get("text"))
    q("DELETE FROM file WHERE id=?", (big,))
    admin_set(max_mb=1024, max_schedule="weekly")
    st, r = call("PUT", "/api/account/backup/settings", {"schedule": "daily"})
    ok(f"Admin: hoechstens woechentlich -> taeglich abgelehnt ({st})", st == 400)

    # --- Zeitplan (Logik + eine Runde) ---
    from saga import userbackup as ub
    from datetime import datetime
    st, r = call("PUT", "/api/account/backup/settings", {"schedule": "weekly", "time": "03:00", "weekday": 2, "keep": 3})
    ok(f"Zeitplan woechentlich gespeichert ({st})", st == 200 and r["schedule"] == "weekly" and r["next"])
    cfg, _, stt = ub.load(A)
    nx = datetime.fromtimestamp(r["next"], ub.TZ)
    ok(f"Naechster Lauf: Mittwoch 03:00 ({nx})", nx.weekday() == 2 and (nx.hour, nx.minute) == (3, 0))
    later = r["next"] + 60
    ok("Faellig nach dem Zeitpunkt, nicht davor", ub.due(cfg, stt, later) and not ub.due(cfg, stt, r["next"] - 60))
    n_before = q("SELECT COUNT(*) FROM user_backup_run WHERE uid=?", (A,))[0][0]
    ok("Zeitplan-Runde startet den Lauf", ub.tick(now=later) >= 1 and q("SELECT COUNT(*) FROM user_backup_run WHERE uid=? AND trigger='zeitplan' AND ok=1",
                                                                         (A,))[0][0] >= 1)
    ok("Gleicher Zeitpunkt nicht zweimal", ub.tick(now=later + 30) == 0 and q("SELECT COUNT(*) FROM user_backup_run WHERE uid=?", (A,))[0][0] == n_before + 1)
    call("PUT", "/api/account/backup/settings", {"schedule": "off"})
    admin_set(max_schedule="daily")

    # --- Admin-Schalter ---
    admin_set(enabled=False)
    st, s = call("GET", "/api/account/backup")
    ok("Abgeschaltet: Status sagt es", st == 200 and not s["allowed"])
    st, r = call("POST", "/api/account/backup/run")
    ok(f"Abgeschaltet: kein Lauf ({st})", st == 403)
    st, r = call("POST", "/api/account/backup/restore/upload", raw=b"x" * 100)
    ok(f"Abgeschaltet: kein Einspielen ({st})", st == 403)
    st, r = call("POST", "/api/account/backup/connect", {"url": NCB})
    ok(f"Abgeschaltet: kein Verbinden ({st})", st == 403)
    ok("Abgeschaltet: Zeitplan laeuft nicht", ub.tick(now=time.time() + 30 * 86400) == 0)
    admin_set(enabled=True)

    # --- Freigabe zurueckgenommen -> Ziel nicht mehr erlaubt ---
    admin_set(hosts="")
    st, r = call("POST", "/api/account/backup/test")
    ok(f"Freigabe zurueckgenommen: Ziel gesperrt ({st})", st == 409)
    admin_set(hosts=f"127.0.0.1:{NCP}, 127.0.0.1:{DVP}")

    # --- Trennen widerruft ---
    rv = stats()["revoked"]
    st, r = call("DELETE", "/api/account/backup/target")
    ok(f"Trennen widerruft das App-Passwort ({r})", st == 200 and r.get("revoked") and stats()["revoked"] == rv + 1)
    st, s = call("GET", "/api/account/backup")
    ok("Getrennt: kein Ziel, Schluessel bleiben", s["target"] is None and len(s["keys"]) == 2
       and q("SELECT secret FROM user_backup WHERE uid=?", (A,))[0][0] == "")

    # --- Schlichtes WebDAV fuer B (Benutzer/Passwort) ---
    urllib.request.urlopen(DVB + "/_ctl?" + urllib.parse.urlencode({"pw": "bb:bb-pass-123"}), timeout=10).read()
    st, r = call("POST", "/api/account/backup/connect", {"url": DVB + "/dav", "user": "bb", "password": "falsch"}, uid=BB)
    ok(f"WebDAV: falsches Passwort abgelehnt ({st})", st == 400)
    st, r = call("POST", "/api/account/backup/connect", {"url": DVB + "/dav", "user": "bb", "password": "bb-pass-123", "folder": "B/Backups"}, uid=BB)
    ok(f"WebDAV verbunden ({st})", st == 200 and r.get("kind") == "webdav")
    call("POST", "/api/account/backup/key", {"key": pub2}, uid=BB)
    st, r, s = run_now(BB)
    ok(f"B: Lauf auf WebDAV ok ({s and s['runs'][0]})", s and s["runs"][0]["ok"])
    bfile = sorted(k for k in stats(DVB)["files"] if k.endswith(".zip.age"))
    zb = None
    if bfile:
        p = os.path.join(TMP, "b.age")
        with open(p, "wb") as f:
            f.write(fake_get(bfile[-1], DVB))
        zp = age_dec(p, kp)
        zb = zipfile.ZipFile(zp) if zp else None
    db_b = json.loads(zb.read("daten.json")) if zb else {"notes": []}
    braw = zb.read("daten.json") if zb else b""
    ok("B's Backup: eigene Seite drin, NICHTS von A (auch nicht die mit B geteilte Seite, nicht A's Kommentar)",
       zb and pb in {n["id"] for n in db_b["notes"]} and p1 not in {n["id"] for n in db_b["notes"]} and b"Projekt " not in braw
       and b"A-AUF-FREMDER-SEITE" not in braw and b"EIGENER-KOMMENTAR" not in braw)

    # --- Kontoloeschung ---
    st, up = call("POST", "/api/account/backup/restore/upload", raw=raw, uid=NEW)
    from saga.userdata import delete_user_data
    call("POST", "/api/account/backup/key", {"key": pub2}, uid=NEW)
    delete_user_data(NEW, True)
    ok("Kontoloeschung: Einstellungen weg", not q("SELECT 1 FROM user_backup WHERE uid=?", (NEW,)))
finally:
    for u in (A, BB):
        q("DELETE FROM user_backup WHERE uid=?", (u,))
        q("DELETE FROM user_backup_run WHERE uid=?", (u,))
    q("DELETE FROM note_comment WHERE body IN ('EIGENER-KOMMENTAR','FREMDER-KOMMENTAR','A-AUF-FREMDER-SEITE')")
    q("DELETE FROM note_share WHERE (owner_uid=? AND target=?) OR (owner_uid=? AND target=?)", (BB, A, A, BB))
    q("DELETE FROM note_version WHERE uid=? AND title='Projekt alt'", (A,))
    q("DELETE FROM note WHERE uid IN (?,?) AND (title LIKE ? OR title IN ('Kunden','Kunde Eins','Auftraege','Auftrag 1','Uebersicht'))",
      (A, BB, f"%{MARK}%"))
    q("DELETE FROM task WHERE text LIKE ?", (f"%{MARK}%",))
    q("DELETE FROM bookmark WHERE url LIKE ?", (f"%{MARK}%",))
    q("DELETE FROM notification WHERE kind='userbackup.failed'")
    for fid in [r[0] for r in (q("SELECT id FROM file WHERE uid IN (?,?) AND name IN ('bild.png','nur-privat.bin','fremd.png','gross.bin')", (A, BB)) or [])]:
        q("DELETE FROM file WHERE id=?", (fid,))
        try:
            os.remove(os.path.join(FILES, fid))
        except OSError:
            pass
    try:
        auth.save_setting("user_backup", {"enabled": True, "max_mb": 1024, "min_h": 1, "max_schedule": "daily", "hosts": ""})
    except Exception:  # noqa: BLE001
        pass
    if auth.get_user(NEW):
        from saga.userdata import delete_user_data
        delete_user_data(NEW, True)
    import shutil
    shutil.rmtree(TMP, ignore_errors=True)

n = len(res)
print(f"{sum(res)}/{n} OK  " + ("ALLES GRUEN" if all(res) and n else "FEHLER"))
