"""Foederation 2.4 E2: Videos im Chat ueber Servergrenzen (chatfile2) mit zwei eigenen Instanzen A und B.

A laeuft mit saga-media vor uvicorn (SAGA_MEDIA_BIN, sonst saga-media im PATH, sonst go build), B nur mit Python - so sind
alle vier Wege dabei: A liefert Teile per saga-media (Python prueft/signiert nur), B holt per Python (Ersatzweg); B liefert per
Python, A holt per saga-media (Bytes nie durch Python). FED_VID_MEDIA=0: beide nur Python.

Geprueft: Faehigkeit chatfile2 ausgehandelt; grosser Anhang (saga-file/v2, > ATTACH_MAX_MB) per fortsetzbarem Upload, Nachricht
ueber Servergrenzen; Empfaenger-Server holt NICHT alles, sondern Anfang/Ende vorab und weitere Bereiche bei Bedarf (lueckenhafte
Zwischenkopie, Bitmap in 1-MiB-Einheiten); Range-Abruf durch die App mit Entschluesselung je Block; Zeitmessung kalt/warm
(PERF-Zeilen); vollstaendig geholt -> normale Zwischenkopie; Teile signiert je Bereich (Range in der signierten Query):
unsignierter/fremder Abruf, uebergrosser Teil (> 4 MiB), ungueltiger Bereich, ganze Datei ohne range (413); falscher Digest in
der Antwort wird abgewiesen (Pruefung der Empfaenger-Seite); Loeschen entfernt auch die lueckenhafte Kopie; Cache-Grenze zaehlt
geholte Einheiten.

Kompatibilitaet: FED_B_23=1 mit FED_B_APP=<Saga 2.3> -> B ohne chatfile2: Upload eines grossen Videos in den Chat mit B wird
abgewiesen (409, Hinweis), Senden eines grossen Anhangs ebenso; kleine Anhaenge (auch v2 bis 10 MB) gehen wie bisher
(B holt sie ganz); keine Serverfehler. Raeumt auf (Datenbanken geloescht)."""
import hashlib, json, os, secrets, shutil, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foederation as T  # noqa: E402
import foederation2 as F2  # noqa: E402
import foederation_chat as FC  # noqa: E402  (App-Klasse User, Helfer)
import chatlib as C  # noqa: E402
from saga import fedsig  # noqa: E402

B23 = os.environ.get("FED_B_23") == "1"
T.I["a"].update(port=8395, admin=8394, db="saga_fva", url="http://127.0.0.1:8395", host="127.0.0.1:8395", state="/tmp/fedvid-a", allow="localhost:8397")
T.I["b"].update(port=8397, admin=8396, db="saga_fvb", url="http://localhost:8397", host="localhost:8397", state="/tmp/fedvid-b", allow="127.0.0.1:8395")
HA, HB = T.I["a"]["host"], T.I["b"]["host"]
ok, hreq, wait, jd, sql = T.ok, T.hreq, T.wait, T.jd, T.sql
api, raw, User = FC.api, FC.raw, FC.User
MiB = 1 << 20
PERF = {}


def media_bin():
    if os.environ.get("FED_VID_MEDIA") == "0":
        return None
    b = os.environ.get("SAGA_MEDIA_BIN") or shutil.which("saga-media")
    if b:
        return b
    if shutil.which("go"):
        out = "/tmp/fedvid-saga-media"
        r = subprocess.run(["go", "build", "-o", out, "."], cwd=os.path.join(T.APP, "media"), capture_output=True, text=True,
                           env=dict(os.environ, CGO_ENABLED="0"))
        if r.returncode == 0:
            return out
    return None


MEDIA = media_bin()
_env_f2 = T.env_of


def env_of(k):
    e = _env_f2(k)
    e["ATTACH_MAX_MB"] = "2"                       # kleine Grenze: "gross" ab 2 MB (schneller Test)
    e["FED_CHAT_CACHE_MB"] = "64"
    if k == "a" and MEDIA:
        e["MEDIA_INTERNAL_PORT"] = "8393"
        e["MEDIA_AUTHZ_TTL"] = "3"
    return e


def start(k):
    i = T.I[k]
    os.makedirs(i["state"], exist_ok=True)
    log = open(os.path.join(i["state"], "server.log"), "a")
    cmd = [sys.executable, "run.py"]
    if k == "a" and MEDIA:
        cmd = [MEDIA, "--"] + cmd
    T.procs[k] = subprocess.Popen(cmd, env=env_of(k), cwd=F2.B_APP if k == "b" else T.APP, stdout=log, stderr=subprocess.STDOUT)
    ok(f"{k.upper()}: gestartet" + (" (saga-media)" if k == "a" and MEDIA else ""), wait(lambda: T._health(k), 40))


T.env_of, T.start = env_of, start
F2.env_of, F2.start = env_of, start


def get(k, uid, fid, rng=None):
    h = {"X-Saga": "1"}
    if rng:
        h["Range"] = rng
    t0 = time.time()
    s, hd, data = hreq(k, "GET", f"/api/chat/files/{fid}", hdr=h, cookie=T.ck(k, uid), timeout=60)
    return s, hd, data, (time.time() - t0) * 1000


def upload_v2(k, uid, cid, blob, part=3 * MiB):
    s, d = api(k, uid, "POST", f"/api/chats/{cid}/uploads", {"size": len(blob), "fmt": 2})
    if s != 200:
        return s, d
    up, off = d["upload"], 0
    while off < len(blob):
        chunk = blob[off:off + part]
        s, h, data = hreq(k, "PUT", f"/api/chat/uploads/{up}", raw=chunk, cookie=T.ck(k, uid),
                          hdr={"X-Saga": "1", "Content-Type": "application/octet-stream", "Content-Range": f"bytes {off}-{off + len(chunk) - 1}/{len(blob)}"})
        if s != 200:
            return s, jd(data)
        off += len(chunk)
    return s, jd(data)


def have_units(k, fid):
    r = sql(k, "SELECT encode(have, 'hex'), cached FROM chat_file_fed WHERE id=?", (fid,))
    if not r:
        return None, None
    hx, cached = r[0]
    bits = bytes.fromhex(hx or "")
    return [u for u in range(len(bits) * 8) if bits[u >> 3] >> (u & 7) & 1], cached


def plain_range(k, uid, fid, key, size, a, b):
    i, j, fr, to = C.f2_range(size, a, b)
    s, _, head, _ = get(k, uid, fid, "bytes=0-47")
    s2, _, body, ms = get(k, uid, fid, f"bytes={fr}-{to}")
    if s != 206 or s2 != 206:
        return None
    out = b""
    for n in range(i, j + 1):
        off = C.f2_block_off(n) - fr
        out += C.f2_open(head, n, body[off:off + C.F2_CS + 16], key)
    return out[a - i * C.F2_CS:b - i * C.F2_CS + 1]


def chat_pair(anna, bert):
    s, d = api("a", "fa-anna", "POST", "/api/chats", {"kind": "dm", "handle": bert.handle, "device": anna.dev["id"]})
    ch = d.get("chat") or {}
    ok(f"A: Direkt-Chat mit Bert ({s})", s == 200 and ch.get("v") == 2)
    bc = wait(lambda: bert.chat(ch["ref"]), 15)
    s, d = api("b", "fb-bert", "POST", f"/api/chats/{bc['id']}/request", {"action": "accept"})
    ok(f"B: angenommen ({s})", s == 200)
    return ch, bert.chat(ch["ref"])


def direction(src, dst, su, du, sender, receiver, sch, dch, label):
    """sender (auf src) schickt ein grosses Video, receiver (auf dst) spielt es bereichsweise ab."""
    plain = os.urandom(13 * MiB + 54321)
    blob, key = C.encrypt_file_f2(plain)
    size = len(blob)
    s, d = upload_v2(src, su, sch["id"], blob)
    fid = d.get("id")
    ok(f"{label}: grosses Video hochgeladen ({s}, {size} Bytes, fmt 2)", s == 200 and d.get("fmt") == 2 and d.get("size") == size)
    s, d, m = sender.send(sch, "Video", files=[{"id": fid, "k": C.b64(key), "n": "film.mp4", "m": "video/mp4", "s": len(plain), "x": 2,
                                               "cs": C.F2_CS, "dur": 12.5, "w": 1280, "h": 720}])
    ok(f"{label}: Nachricht mit grossem Anhang ueber Servergrenzen ({s})", s == 200)
    ok(f"{label}: angekommen (Anhang beim Absender-Server angekuendigt)",
       wait(lambda: sql(dst, "SELECT size FROM chat_file f JOIN chat_file_fed x ON x.id=f.id WHERE f.id=?", (fid,)) == [[size]], 20))
    units = -(-size // MiB)
    got = wait(lambda: (have_units(dst, fid)[0] or []) == [0, units - 1], 20)
    ok(f"{label}: vorab nur Anfang und Ende geholt (1 MiB + letzte Einheit), nicht alles", got and not os.path.exists(
        os.path.join(T.I[dst]["state"], "chat", fid)))
    # Abspielen: Kopf, Anfang - kommt aus der Zwischenkopie
    s, hd, data, ms = get(dst, du, fid, "bytes=0-1048575")
    ok(f"{label}: erste MiB (vorab geholt) -> 206 ({ms:.0f} ms)", s == 206 and data == blob[:MiB] and hd.get("content-range") == f"bytes 0-{MiB - 1}/{size}")
    PERF[f"{label} warm 1 MiB ms"] = round(ms, 1)
    # Mitte (kalt): fehlender Bereich wird jetzt geholt
    a = 6 * MiB + 100
    s, hd, data, ms = get(dst, du, fid, f"bytes={a}-{a + MiB - 1}")
    ok(f"{label}: Mitte kalt -> 206 korrekt ({ms:.0f} ms)", s == 206 and data == blob[a:a + MiB])
    PERF[f"{label} kalt 1 MiB ms"] = round(ms, 1)
    s, hd, data, ms2 = get(dst, du, fid, f"bytes={a}-{a + MiB - 1}")
    ok(f"{label}: Mitte warm -> 206 ({ms2:.0f} ms)", s == 206 and data == blob[a:a + MiB])
    PERF[f"{label} warm 1 MiB (2.) ms"] = round(ms2, 1)
    have, cached = have_units(dst, fid)
    ok(f"{label}: Bitmap: nur geholte Einheiten ({have})", cached == 0 and set(have) == {0, 6, 7, units - 1})
    ok(f"{label}: Klartext ueber Range + Bloecke (wie die App)", plain_range(dst, du, fid, key, len(plain), 3 * MiB, 5 * MiB + 77) == plain[3 * MiB:5 * MiB + 78])
    ok(f"{label}: fremdes Konto -> 404", get(dst, "fa-carl" if dst == "a" else "fb-olga", fid, "bytes=0-9")[0] == 404)
    # ohne Range: ganzer Abruf (holt den Rest; saga-media in Schritten, Python je Antwort bis 8 MiB)
    s, hd, data, ms = get(dst, du, fid)
    if s == 200:
        whole = data
    else:                                           # Python-Ersatzweg: 206 mit Teil, Rest nachfordern wie ein Player
        whole = data
        while s == 206 and len(whole) < size:
            s, hd, data, _ = get(dst, du, fid, f"bytes={len(whole)}-")
            whole += data if s == 206 else b""
    ok(f"{label}: ganz abgerufen (Rest geholt) -> gleicher Blob, entschluesselbar", hashlib.sha256(whole).digest() == hashlib.sha256(blob).digest()
       and C.decrypt_file_f2(whole, key) == plain)
    ok(f"{label}: vollstaendig -> normale Zwischenkopie (cached=1, Bitmap leer)",
       wait(lambda: have_units(dst, fid) == ([], 1) and os.path.getsize(os.path.join(T.I[dst]["state"], "chat", fid)) == size, 10)
       and not os.path.exists(os.path.join(T.I[dst]["state"], "chat", "fed", fid)))
    return fid, size, m


def part_new(anna, bert, ch, bc):
    ok("chatfile2 ausgehandelt (A-B)", wait(lambda: "chatfile2" in sql("a", "SELECT caps FROM fed_peer")[0][0]
                                            and "chatfile2" in sql("b", "SELECT caps FROM fed_peer")[0][0], 20))
    fid_ab, size_ab, m_ab = direction("a", "b", "fa-anna", "fb-bert", anna, bert, ch, bc, "A->B")
    fid_ba, size_ba, m_ba = direction("b", "a", "fb-bert", "fa-anna", bert, anna, bc, ch, "B->A")
    if MEDIA:
        L = open(os.path.join(T.I["a"]["state"], "server.log")).read()
        ok("A: Teile fuer B lieferte saga-media (Zugriffszeilen /fed/v2/chat/files ... 206)",
           f"/fed/v2/chat/files/{fid_ab}?range=" in L and "\" 206 Partial Content [saga-media" in L)
        mine = [x for x in L.splitlines() if f"GET /api/chat/files/{fid_ba}" in x]
        ok("A: Video von B lieferte saga-media aus der lueckenhaften Kopie (Python nur Plan/Pruefung, keine Bytes)",
           any("206" in x and "[saga-media" in x for x in mine) and not any("[saga-media" not in x and ('" 206' in x or '" 200' in x) for x in mine))
    # --- Signatur und Grenzen des Range-Abrufs (B fragt A von Hand) ------------------------------------------------------
    privb = FC.own_priv("b")
    s, h, d, nonce, _ = T.signed("a", privb, T.I["b"]["url"], "GET", f"/fed/v2/chat/files/{fid_ab}", query="range=0-99")
    pub_a = fedsig.unb64u(T.py("a", "from saga import federation, fedsig\npriv, pub = federation.own_key()\nprint(json.dumps(fedsig.b64u(pub)))"))
    good = True
    try:
        fedsig.verify_response(pub_a, T.I["a"]["url"], s, h, d, nonce)
    except fedsig.SigError:
        good = False
    ok(f"B->A: Teil 0-99 -> 206, signiert (Digest ueber den Teil), Saga-Size ({s})", s == 206 and good and len(d) == 100
       and h.get("saga-size") == str(size_ab) and h.get("content-range") == f"bytes 0-99/{size_ab}")
    try:
        fedsig.verify_response(pub_a, T.I["a"]["url"], s, dict(h, **{"content-digest": fedsig.digest(d[:99] + b"x")}), d[:99] + b"x", nonce)
        bad_ok = True
    except fedsig.SigError:
        bad_ok = False
    ok("B: veraenderter Teil (anderer Digest) wird abgewiesen", not bad_ok)
    s, *_ = T.signed("a", privb, T.I["b"]["url"], "GET", f"/fed/v2/chat/files/{fid_ab}", query=f"range=0-{4 * MiB}")
    ok(f"B->A: Teil groesser als 4 MiB -> abgewiesen ({s})", s == 416)
    s, *_ = T.signed("a", privb, T.I["b"]["url"], "GET", f"/fed/v2/chat/files/{fid_ab}", query=f"range={size_ab}-{size_ab + 5}")
    ok(f"B->A: Bereich hinter dem Ende -> 416 ({s})", s == 416)
    s, *_ = T.signed("a", privb, T.I["b"]["url"], "GET", f"/fed/v2/chat/files/{fid_ab}")
    ok(f"B->A: ganze Datei ohne range (gross) -> 413 ({s})", s == 413)
    s, h, d = hreq("a", "GET", f"/fed/v2/chat/files/{fid_ab}?range=0-99")
    ok(f"unsignierter Abruf -> 401 ({s})", s == 401)
    s, *_ = T.signed("a", privb, T.I["b"]["url"], "GET", f"/fed/v2/chat/files/{secrets.token_urlsafe(18)}", query="range=0-9")
    ok(f"B->A: unbekannte Datei -> 404 ({s})", s == 404)
    hdr, nonce = fedsig.sign_request(privb, T.I["b"]["url"], "GET", HA, f"/fed/v2/chat/files/{fid_ab}", "range=0-9", b"")
    s1, *_ = hreq("a", "GET", f"/fed/v2/chat/files/{fid_ab}?range=0-9", hdr=hdr)
    s2, *_ = hreq("a", "GET", f"/fed/v2/chat/files/{fid_ab}?range=0-9", hdr=hdr)
    ok(f"B->A: Wiederholung derselben signierten Anfrage -> 401 ({s1}, {s2})", s1 == 206 and s2 == 401)
    hdr, nonce = fedsig.sign_request(privb, T.I["b"]["url"], "GET", HA, f"/fed/v2/chat/files/{fid_ab}", "range=0-9", b"")
    s, *_ = hreq("a", "GET", f"/fed/v2/chat/files/{fid_ab}?range=0-99999", hdr=hdr)
    ok(f"B->A: Bereich in der Query veraendert (Signatur passt nicht) -> 401 ({s})", s == 401)
    # Empfaenger prueft die Antwort: falscher Digest -> abgewiesen (Python prueft, was saga-media meldet)
    r = T.py("b", f"""
from saga import fed_chat as FCH
pl = FCH.plan({fid_ab!r}, 0, 99) or {{}}
print(json.dumps(pl.get("fetch") and pl["fetch"][0]["nonce"] or ""))""")
    res = T.py("b", f"""
from saga import fed_chat as FCH, fedsig
print(json.dumps(FCH.got({r!r}, 206, {{}}, fedsig.digest(b"x"), 1)))""")
    ok("B: Antwort ohne gueltige Signatur/Plan -> nicht uebernommen", res.get("ok") is False)
    # --- Loeschen: lueckenhafte Kopie weg -------------------------------------------------------------------------------
    plain = os.urandom(5 * MiB)
    blob, key = C.encrypt_file_f2(plain)
    s, d = upload_v2("a", "fa-anna", ch["id"], blob)
    fid = d.get("id")
    s, d, mx = anna.send(ch, "gleich weg", files=[{"id": fid, "k": C.b64(key), "n": "x.mp4", "m": "video/mp4", "s": len(plain), "x": 2, "cs": C.F2_CS}])
    ok("B: lueckenhafte Kopie entsteht (vorab)", wait(lambda: os.path.exists(os.path.join(T.I["b"]["state"], "chat", "fed", fid)), 20))
    ok("A: Nachricht fuer alle loeschen", api("a", "fa-anna", "DELETE", f"/api/chats/{ch['id']}/messages/{mx['id']}")[0] == 200)
    ok("B: Anhang und lueckenhafte Kopie weg", wait(lambda: not sql("b", "SELECT 1 FROM chat_file WHERE id=?", (fid,))
                                                   and not os.path.exists(os.path.join(T.I["b"]["state"], "chat", "fed", fid)), 15))
    # --- Cache-Grenze zaehlt geholte Einheiten --------------------------------------------------------------------------
    n = T.py("b", f"""
from saga import fed_chat as FCH
FCH.CACHE_PEER_MAX = 2 * FCH.UNIT
print(json.dumps(FCH.cache_trim({sql('b', 'SELECT id FROM fed_peer')[0][0]})))""")
    ok(f"B: Cache-Grenze (2 MiB) raeumt Zwischenkopien ab ({n})", isinstance(n, int) and n >= 1)
    s, hd, data, ms = get("b", "fb-bert", fid_ab, "bytes=0-99")
    ok(f"B: danach bei Bedarf neu geholt ({s}, {ms:.0f} ms)", s == 206)
    PERF["A->B nach Raeumen 100 B ms"] = round(ms, 1)


def part_23(anna, bert, ch, bc):
    ok("A: B (2.3) ohne chatfile2", wait(lambda: sql("a", "SELECT caps_at FROM fed_peer")[0][0] is not None, 20)
       and "chatfile2" not in sql("a", "SELECT caps FROM fed_peer")[0][0] and "chat2" in sql("a", "SELECT caps FROM fed_peer")[0][0])
    blob, key = C.encrypt_file_f2(os.urandom(3 * MiB))
    s, d = api("a", "fa-anna", "POST", f"/api/chats/{ch['id']}/uploads", {"size": len(blob), "fmt": 2})
    ok(f"A: grosses Video in den Chat mit 2.3-Server -> 409 mit Hinweis ({s})", s == 409 and "noch nicht" in d.get("error", ""))
    # grosser Anhang auf anderem Weg (z. B. alte Kennung) - Senden wird trotzdem abgewiesen
    fid = secrets.token_urlsafe(18)
    os.makedirs(os.path.join(T.I["a"]["state"], "chat"), exist_ok=True)
    open(os.path.join(T.I["a"]["state"], "chat", fid), "wb").write(blob)
    sql("a", "INSERT INTO chat_file (id, chat_id, uid, size, created, fmt) VALUES (?,?,?,?,?,2)", (fid, ch["id"], "fa-anna", len(blob), time.time()))
    s, d, _ = anna.send(ch, "zu gross", files=[{"id": fid, "k": C.b64(key), "n": "v.mp4", "m": "video/mp4", "x": 2}])
    ok(f"A: Senden eines grossen Anhangs an 2.3 -> 409 ({s})", s == 409 and "noch nicht" in d.get("error", ""))
    # kleine Anhaenge (v1 und v2 bis ATTACH_MAX_MB) gehen wie bisher
    small, k2 = C.encrypt_file_f2(os.urandom(700 * 1024))
    s, d = upload_v2("a", "fa-anna", ch["id"], small)
    f2 = d.get("id")
    ok(f"A: kleines v2-Video an 2.3 erlaubt ({s})", s == 200)
    s, d, _ = anna.send(ch, "klein", files=[{"id": f2, "k": C.b64(k2), "n": "k.mp4", "m": "video/mp4", "x": 2}])
    ok(f"A -> B (2.3): Nachricht mit kleinem v2-Anhang ({s})", s == 200)
    ok("B (2.3): angekommen", wait(lambda: "klein" in bert.texts(bc), 15))
    s, hd, data, _ = get("b", "fb-bert", f2)
    ok(f"B (2.3): holt den kleinen v2-Anhang ganz (ohne range) und er ist entschluesselbar ({s})", s == 200 and data == small)
    v1, k1 = C.encrypt_file_v2(bc["ref"], b"bild" * 1000)
    s1, _, d1 = raw("b", "fb-bert", "POST", f"/api/chats/{bc['id']}/files", v1)
    s, d, _ = bert.send(bc, "von 2.3", files=[{"id": jd(d1)["id"], "k": C.b64(k1), "n": "a.txt"}])
    ok(f"B (2.3) -> A: Anhang wie bisher ({s})", s == 200 and wait(lambda: "von 2.3" in anna.texts(ch), 15))
    s, hd, data, _ = get("a", "fa-anna", jd(d1)["id"])
    ok(f"A: Anhang von 2.3 abrufbar ({s})", s == 200 and data == v1)


def run():
    try:
        A, B, pa, pb = F2.main()
        anna, bert = User("a", "fa-anna"), User("b", "fb-bert")
        ch, bc = chat_pair(anna, bert)
        if B23:
            part_23(anna, bert, ch, bc)
        else:
            part_new(anna, bert, ch, bc)
    except Exception:
        import traceback
        traceback.print_exc()
        T.res.append(False)
    finally:
        T.stop_all()
        for k in T.I:
            n, txt = T.server_errors(k)
            ok(f"{k.upper()}: Serverfehler {n}", n == 0)
            if n:
                print(txt, flush=True)
        for k in T.I:
            try:
                T.pg_admin(f"DROP DATABASE IF EXISTS {T.I[k]['db']} WITH (FORCE)")
            except Exception as ex:  # noqa: BLE001
                print("aufraeumen:", ex)
            shutil.rmtree(T.I[k]["state"], ignore_errors=True)
    for k, v in PERF.items():
        print(f"PERF {json.dumps({'messung': 'foederation_videos', 'punkt': k, 'wert': v})}")
    res = T.res
    print(f"\nFOEDERATION-VIDEOS {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    run()
