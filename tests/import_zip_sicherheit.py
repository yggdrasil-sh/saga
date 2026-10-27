"""Import: Sicherheit des Unterbaus (2.5, Paket 2 E2, saga/import_common.py) mit selbst gebauten boesen Archiven.

Prueft: ZIP-Bombe (hohes Verhaeltnis), gelogene Groessen im ZIP-Kopf (Lesen zaehlt mit), Pfad-Traversal (../, absolut,
Laufwerksbuchstabe, Backslash, Steuerzeichen), Symlink-Eintraege, verschachtelte ZIPs, zu viele Eintraege (> 50 000),
Summe entpackt ueber IMPORT_UNPACKED_MAX_MB, Upload ueber IMPORT_MAX_MB (413), kaputtes ZIP, nichts wird ausserhalb der
Ablage geschrieben, Upload-Ordner nach dem Lauf weg, fremde Personen sehen nichts, kein Serverfehler. Optional
Speicherspitze des Servers (SAGA_SERVER_PID): eine 40-MB-CSV im ZIP laesst den Server um weniger als 150 MB wachsen.
Server fuer diesen Test mit IMPORT_MAX_MB=64 und IMPORT_UNPACKED_MAX_MB=128 starten (sonst werden diese Faelle
uebersprungen). Wegwerf-Konten iz-*."""
import base64
import http.client
import io
import json
import os
import stat
import struct
import sys
import time
import urllib.parse
import zipfile

from itsdangerous import TimestampSigner

import auth
from db import connect

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
STATE = os.environ.get("STATE_DIR", "/data")
A, BU = "iz-a", "iz-b"
MAXMB = int(os.environ.get("IMPORT_MAX_MB", "512"))
UNPMB = int(os.environ.get("IMPORT_UNPACKED_MAX_MB", "2048"))
res = []


def ok(n, c, detail=None):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, flush=True)
    if not c and detail is not None:
        print("     ", str(detail)[:1500], flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, body=None, u=A, raw=None, headers=None):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=300)
    h = {"Host": HOST, "Accept-Language": "de", "X-Saga": "1", "Cookie": "saga=" + ck(u)}
    h.update(headers or {})
    data = None
    if raw is not None:
        data, h["Content-Type"] = raw, "application/octet-stream"
    elif body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    c.request(m, p, body=data, headers=h)
    r = c.getresponse()
    d = r.read()
    try:
        return r.status, json.loads(d) if d[:1] in (b"[", b"{") else d
    except ValueError:
        return r.status, d


def upload(name, data, u=A):
    return req("POST", "/api/import/upload?name=" + urllib.parse.quote(name), raw=data, u=u)


def analyze(data, name="boese.zip", body=None):
    s, d = upload(name, data)
    if s != 200:
        return s, d, None
    s, a = req("POST", f"/api/import/{d['token']}/analyze", body or {"kind": "csv"})
    return s, a, d["token"]


def wait(rid, ms=120000):
    t0 = time.time()
    while time.time() - t0 < ms / 1000:
        s, d = req("GET", f"/api/import/runs/{rid}")
        if s == 200 and d["state"] in ("done", "failed", "cancelled"):
            return d
        time.sleep(0.3)
    return d


def zbytes(entries, method=zipfile.ZIP_DEFLATED):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", method) as z:
        for e in entries:
            if isinstance(e, zipfile.ZipInfo):
                z.writestr(e, e.comment or b"")
                e.comment = b""
            else:
                z.writestr(e[0], e[1])
    return buf.getvalue()


def codes(a):
    return {g["code"]: [x["path"] for x in g["items"]] for g in (a.get("report") or {}).get("groups", [])}


def lie_size(data, name, fake):
    """file_size eines Eintrags im lokalen Kopf und im zentralen Verzeichnis faelschen (Kopfangaben luegen)."""
    b = bytearray(data)
    nb = name.encode()
    i = b.find(b"PK\x03\x04")
    while i >= 0:
        n = struct.unpack("<H", b[i + 26:i + 28])[0]
        if bytes(b[i + 30:i + 30 + n]) == nb:
            b[i + 22:i + 26] = struct.pack("<I", fake)
        i = b.find(b"PK\x03\x04", i + 4)
    i = b.find(b"PK\x01\x02")
    while i >= 0:
        n = struct.unpack("<H", b[i + 28:i + 30])[0]
        if bytes(b[i + 46:i + 46 + n]) == nb:
            b[i + 24:i + 28] = struct.pack("<I", fake)
        i = b.find(b"PK\x01\x02", i + 4)
    return bytes(b)


def rss(pid):
    try:
        for line in open(f"/proc/{pid}/status"):
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) // 1024
    except OSError:
        return None


def main():
    from saga.i18n import set_lang
    set_lang("de")
    cleanup()
    for u in (A, BU):
        auth.create_user(u, "Zip-Pass-1234", u.upper())
    try:
        run()
    finally:
        cleanup()


def run():
    good = b"Name,Zahl\nAlpha,1\nBeta,2\n"
    # --- Pfade ----------------------------------------------------------------------------------------------------
    bad_names = ["../../etc/passwd.csv", "/abs/pfad.csv", "C:/win/x.csv", "a/../../b.csv", "ctrl\x01name.csv", "..\\..\\win.csv"]
    data = zbytes([("ok/daten.csv", good)] + [(n, good) for n in bad_names])
    s, a, tok = analyze(data)
    c = codes(a)
    ok("Pfade: Analyse ok, gute CSV gewaehlt", s == 200 and a.get("path") == "ok/daten.csv" and a.get("rows") == 2, (s, a))
    ok("Pfade: alle boesen Namen im Bericht (bad_path)", len(c.get("bad_path", [])) == len(bad_names), c)
    ok("Pfade: Backslash normalisiert und verworfen", any("win.csv" in x for x in c.get("bad_path", [])), c)
    s, r = req("POST", f"/api/import/{tok}/run", {"kind": "csv", "path": "../../etc/passwd.csv",
                                                  "columns": [{"index": 0, "type": "text"}, {"index": 1, "type": "number"}], "title_col": 0})
    st = wait(r["run_id"]) if s == 202 else {}
    ok("Lauf mit boesem Pfad: nimmt die gute Datei", st.get("state") == "done" and st["report"]["counts"]["rows"] == 2, st)
    ok("nichts ausserhalb geschrieben", not os.path.exists(os.path.join(STATE, "..", "etc", "passwd.csv"))
       and not os.path.exists("/abs/pfad.csv"))
    ok("Upload-Ordner nach dem Lauf weg", not os.path.exists(os.path.join(STATE, "import-tmp", tok)))

    # --- Symlink, verschachteltes ZIP, Bombe ------------------------------------------------------------------------
    li = zipfile.ZipInfo("link.csv")
    li.create_system = 3
    li.external_attr = (stat.S_IFLNK | 0o777) << 16
    li.comment = b"/etc/passwd"
    inner = zbytes([("x.csv", good)])
    bomb = b"0" * (40 * 1024 * 1024)
    data = zbytes([("daten.csv", good), li, ("innen.zip", inner), ("bombe.csv", bomb)])
    ok("Bombe ist wirklich stark komprimiert", len(data) < 1024 * 1024, len(data))
    s, a, tok = analyze(data)
    c = codes(a)
    ok("Symlink verworfen", c.get("symlink") == ["link.csv"], c)
    ok("ZIP im ZIP nicht entpackt", c.get("nested_zip") == ["innen.zip"], c)
    ok("ZIP-Bombe verworfen", c.get("bomb") == ["bombe.csv"], c)
    ok("gute Datei bleibt", s == 200 and a.get("path") == "daten.csv", a)
    s, a, _ = analyze(data, body={"kind": "csv", "path": "bombe.csv"})
    ok("Bombe ausdruecklich gewaehlt: wird nicht genommen", s == 200 and a.get("path") == "daten.csv", (s, a))

    # --- gelogene Groesse: Kopf sagt 30 Bytes, entpackt sind es 3 MB ----------------------------------------------------
    big = b"Name,Zahl\n" + b"".join(b"Z%d,%d\n" % (i, i) for i in range(250000))
    data = lie_size(zbytes([("echt.csv", good), ("luege.csv", big)]), "luege.csv", 30)
    s, a, tok = analyze(data, body={"kind": "csv", "path": "luege.csv"})
    ok("gelogene Groesse: abgelehnt bzw. abgebrochen, kein 500", s in (200, 400) and not (s == 200 and a.get("rows", 0) > 10), (s, str(a)[:300]))

    # --- kaputtes ZIP -------------------------------------------------------------------------------------------------
    s, d = upload("kaputt.zip", b"PK\x03\x04" + os.urandom(500))
    ok("kaputtes ZIP: 400 beim Upload", s == 400, (s, d))
    trunc = zbytes([("a.csv", good * 1000)])[:-30]
    s, a, _ = analyze(trunc, name="abgeschnitten.zip")
    ok("abgeschnittenes ZIP: kein 500", s in (200, 400), s)

    # --- zu viele Eintraege -------------------------------------------------------------------------------------------
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for i in range(50001):
            z.writestr(f"f{i}.txt", b"")
    s, a, _ = analyze(buf.getvalue(), name="viele.zip")
    ok("50 001 Eintraege: abgelehnt (400)", s == 400 and "Einträge" in str(a), (s, a))

    # --- Summe entpackt / Upload-Grenze --------------------------------------------------------------------------------
    if UNPMB <= 256:
        n = UNPMB // 50 + 1
        blk = b"".join(os.urandom(1024) + bytes(50 * 1024) for _ in range(1000))      # ~50 MB, Verhaeltnis ~ 1:50 (keine Bombe)
        data = zbytes([(f"teil{i}.bin", blk) for i in range(n)])
        if len(data) < MAXMB * 1024 * 1024:
            s, a, _ = analyze(data, name="gross.zip")
            ok(f"Summe entpackt > {UNPMB} MB: abgelehnt", s == 400 and "Entpackt" in str(a), (s, a))
    else:
        print("SKIP Summe entpackt (IMPORT_UNPACKED_MAX_MB > 256)")
    if MAXMB <= 128:
        s, d = req("POST", "/api/import/upload?name=riesig.zip", raw=b"x" * 1024, headers={"Content-Length": str(MAXMB * 1024 * 1024 + 5 * 1024 * 1024)})
        ok("Upload ueber IMPORT_MAX_MB: 413", s == 413, (s, d))
    else:
        print("SKIP Upload-Grenze (IMPORT_MAX_MB > 128)")

    # --- fremde Personen ----------------------------------------------------------------------------------------------
    s, d = upload("x.csv", good)
    s2, _ = req("POST", f"/api/import/{d['token']}/analyze", {}, u=BU)
    s3, _ = req("DELETE", f"/api/import/{d['token']}", u=BU)
    ok("fremd: Analyse/Verwerfen 404", s2 == 404 and s3 == 404)

    # --- Speicher (optional) -------------------------------------------------------------------------------------------
    pid = os.environ.get("SAGA_SERVER_PID")
    if pid:
        rows = b"".join(b"Eintrag %d,%d,Text mit etwas Inhalt %d\n" % (i, i, i) for i in range(800000))
        data = zbytes([("gross.csv", b"Name,Nr,Text\n" + rows)])
        before = rss(pid)
        s, a, tok = analyze(data, name="speicher.zip")
        after = rss(pid)
        ok(f"40-MB-CSV im ZIP: Analyse, Server waechst < 150 MB ({before} -> {after} MB)", s == 200 and a.get("rows") == 800000
           and after - before < 150, (s, str(a)[:200]))
    else:
        print("SKIP Speicherspitze (SAGA_SERVER_PID nicht gesetzt)")


def cleanup():
    from saga.userdata import delete_user_data
    for u in (A, BU):
        if auth.get_user(u):
            delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM note WHERE uid LIKE 'iz-%'")
        c.execute("DELETE FROM import_run WHERE uid LIKE 'iz-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'iz-%'")


if __name__ == "__main__":
    main()
    print(f"\nIMPORT-ZIP-SICHERHEIT {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
    sys.exit(0 if res and all(res) else 1)
