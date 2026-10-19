"""Foederation 2.3 E4/E6: Teilen ueber Servergrenzen vollstaendig - zwei eigene Saga-Instanzen A und B (eigene Datenbanken
und Ports, wie tests/foederation2.py). Anna (A) teilt Seiten und Datenbanken mit Personen auf B; die Apps sprechen nur mit
ihrem eigenen Server (negative IDs).

Geprueft: Datenbank oeffnen (Schema mit Relationen auf negative IDs), Zeilen mit Ansicht/Filter/Blaettern, Werte und Schema
bearbeiten, Relation auf eine Zeile einer anderen geteilten Datenbank, Zeile anlegen (= Unterseite mit props), Unterseite
anlegen, Verschieben innerhalb des geteilten Bereichs (und nicht hinaus), Rechte (Lesen/Kommentieren/Bearbeiten, Gruppe auf
B), Bilder/Dateien hochladen (beim Eigentuemer, Vorschau nur fuer die hochladende Person bis zum Speichern, Grenze je
fremder Person), Versionen (ansehen/wiederherstellen ab 'Bearbeiten'), Export (Markdown, ZIP), Anwesenheit + Live ueber ein
kurzlebiges Abo (Ereignisse ohne Inhalt, Abo laeuft ab), Seitenbaum-Abgleich ('Mit mir geteilt' neu laden), Trennen.
Kompatibilitaet: FED_B_APP=<Saga 2.1/2.2> -> Protokoll 1 laeuft weiter, neue Funktionen melden verstaendlich "noch nicht",
keine Serverfehler. Raeumt auf (Datenbanken geloescht)."""
import http.client, io, json, os, socket, sys, threading, time, urllib.parse, zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foederation as T  # noqa: E402
import foederation2 as F2  # noqa: E402

T.I["a"].update(port=8395, admin=8394, db="saga_fta", url="http://127.0.0.1:8395", host="127.0.0.1:8395", state="/tmp/fedteil-a", allow="localhost:8397")
T.I["b"].update(port=8397, admin=8396, db="saga_ftb", url="http://localhost:8397", host="localhost:8397", state="/tmp/fedteil-b", allow="127.0.0.1:8395")
HA, HB = T.I["a"]["host"], T.I["b"]["host"]
ok, hreq, wait, jd, sql, api = T.ok, T.hreq, T.wait, T.jd, T.sql, T.api
COMPAT = F2.COMPAT
WATCH = 8
_env1 = F2.env_of


def env_of(k):
    e = _env1(k)
    e.update(FED_WATCH_TTL=str(WATCH), FED_UPLOAD_QUOTA_MB="1")
    return e


F2.env_of = T.env_of = env_of
SCHEMA = {"props": [{"id": "status", "name": "Status", "type": "select", "options": [{"id": "offen", "name": "Offen", "color": "gray"},
                                                                                   {"id": "fertig", "name": "Fertig", "color": "green"}]},
                    {"id": "zahl", "name": "Zahl", "type": "number"}],
          "views": [{"id": "tabelle", "name": "Tabelle", "type": "table"}]}


def ref(pid, rid):
    return -(rid * 1024 + pid)


def upload(k, uid, note, data, name="t.png", field="image", ctype="image/png"):
    bd = "fedteil"
    mp = (f"--{bd}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{name}\"\r\nContent-Type: {ctype}\r\n\r\n").encode() \
        + data + f"\r\n--{bd}--\r\n".encode()
    s, _, d = hreq(k, "POST", f"/api/files?note={note}", raw=mp, hdr={"X-Saga": "1", "Content-Type": f"multipart/form-data; boundary={bd}"},
                   cookie=T.ck(k, uid))
    return s, jd(d)


def get_raw(k, uid, p):
    s, h, d = hreq(k, "GET", p, hdr={"X-Saga": "1"}, cookie=T.ck(k, uid))
    return s, h, d


class Stream:
    """SSE-Leser (Live-Kanal) eines Kontos auf einer Instanz."""

    def __init__(self, k, uid):
        i = T.I[k]
        self.c = http.client.HTTPConnection("127.0.0.1", i["port"], timeout=300)
        self.c.request("GET", "/api/events", headers={"Host": i["host"], "Accept": "text/event-stream", "Cookie": T.ck(k, uid)})
        self.r = self.c.getresponse()
        self.ev, self.closed = [], False
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        cur = {}
        try:
            while True:
                line = self.r.readline()
                if not line:
                    break
                line = line.decode("utf-8").rstrip("\r\n")
                if line == "":
                    if "event" in cur:
                        self.ev.append((cur.get("event"), json.loads(cur.get("data") or "null")))
                    cur = {}
                elif not line.startswith(":"):
                    a, _, v = line.partition(":")
                    cur[a] = v[1:] if v.startswith(" ") else v
        except Exception:  # noqa: BLE001
            pass
        self.closed = True

    def wait(self, typ, pred=None, since=0, timeout=8.0):
        end = time.time() + timeout
        while time.time() < end:
            for e in self.ev[since:]:
                if e[0] == typ and (pred is None or pred(e[1])):
                    return e[1]
            time.sleep(0.05)
        return None

    def close(self):
        try:
            self.c.sock.shutdown(socket.SHUT_RDWR)
            self.c.close()
        except Exception:  # noqa: BLE001
            pass


def handle_of(k, uid):
    return api(k, uid, "GET", "/api/account/federation")[1].get("handle")


# =====================================================================================================
def setup(pa, pb):
    """Anna (A): Seite P mit Kind C, Bereich Q (anders geteilt), Datenbanken D (Zeilen) und D2 (Ziel einer Relation)."""
    s, d = api("a", "fa-anna", "POST", "/api/notes", {"title": "Projekt"})
    P = d["id"]
    api("a", "fa-anna", "PATCH", f"/api/notes/{P}", {"body": T.blocks(T.P("Start", "b1")), "format": "blocks"})
    C = api("a", "fa-anna", "POST", "/api/notes", {"title": "Kapitel", "parent_id": P})[1]["id"]
    Q = api("a", "fa-anna", "POST", "/api/notes", {"title": "Anderer Bereich", "parent_id": P})[1]["id"]
    D2 = api("a", "fa-anna", "POST", "/api/notes", {"title": "Kunden", "kind": "database", "parent_id": P})[1]["id"]
    k1 = api("a", "fa-anna", "POST", "/api/notes", {"title": "Kunde Eins", "parent_id": D2})[1]["id"]
    D = api("a", "fa-anna", "POST", "/api/notes", {"title": "Aufgaben", "kind": "database", "parent_id": P})[1]["id"]
    sch = json.loads(json.dumps(SCHEMA))
    sch["props"].append({"id": "kunde", "name": "Kunde", "type": "relation", "target": D2})
    s, d = api("a", "fa-anna", "PATCH", f"/api/notes/{D}", {"schema": sch})
    ok(f"A: Datenbank mit Relation angelegt ({s})", s == 200)
    rows = []
    for i in range(5):
        rows.append(api("a", "fa-anna", "POST", "/api/notes", {"title": f"Aufgabe {i}", "parent_id": D,
                                                               "props": {"status": "offen" if i % 2 else "fertig", "zahl": i}})[1]["id"])
    return P, C, Q, D, D2, k1, rows


def part_share(A, B, pa, pb):
    P, C, Q, D, D2, k1, rows = setup(pa, pb)
    hanna = handle_of("b", "fb-anna")
    ok(f"B: Anna-B hat einen Handle ({hanna})", bool(hanna))
    for tg, role in ((f"@bert@{HB}", "edit"), (f"@{hanna}", "view")):
        s, d = api("a", "fa-anna", "PUT", f"/api/notes/{P}/shares", {"type": "remote", "target": tg, "role": role})
        ok(f"A: Projekt mit {tg[:12]}… geteilt ({role}, {s})", s == 200)
    s, d = api("a", "fa-anna", "PUT", f"/api/notes/{Q}/shares", {"type": "remote_group", "target": f"fbteam@{HB}", "role": "comment"})
    ok(f"A: Bereich Q zusaetzlich an Gruppe fbteam@B ({s})", s == 200)
    ok("B: Freigaben angekommen", wait(lambda: len(sql("b", "SELECT 1 FROM fed_inbound")) >= 3, 15))
    ok("A: Gegenstelle meldet db/subpages/upload/versions/export/live/presence",
       wait(lambda: {"db", "subpages", "upload", "versions", "export", "live", "presence"} <= set(json.loads(sql("a", "SELECT caps FROM fed_peer")[0][0])), 20))
    s, d = api("b", "fb-bert", "GET", "/api/fed/shared")
    ids = {x["id"]: x for x in d.get("items", [])}
    rP, rC, rQ, rD, rD2 = (ref(pa, x) for x in (P, C, Q, D, D2))
    ok(f"B: 'Mit mir geteilt' enthaelt Seite, Kind, Datenbanken und Zeilen ({len(ids)})",
       {rP, rC, rQ, rD, rD2, ref(pa, rows[0])} <= set(ids) and ids[rD]["kind"] == "database")
    return P, C, Q, D, D2, k1, rows


def part_db(A, B, pa, pb, P, C, Q, D, D2, k1, rows):
    rP, rD, rD2 = ref(pa, P), ref(pa, D), ref(pa, D2)
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rD}")
    sch = d.get("schema") or {}
    rel = next((x for x in sch.get("props", []) if x["type"] == "relation"), {})
    ok(f"B: Datenbank geoeffnet (kind database, Rolle edit, Schema da) ({s})", s == 200 and d.get("kind") == "database" and d.get("role") == "edit"
       and len(sch.get("props", [])) == 3)
    ok(f"B: Relation zeigt auf negative ID der Ziel-Datenbank ({rel.get('target')})", rel.get("target") == rD2)
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rD}/rows")
    ok(f"B: Zeilen der Datenbank ({s}, {len(d.get('rows', []))})", s == 200 and len(d["rows"]) == 5 and all(r["id"] < 0 for r in d["rows"])
       and d.get("role") == "edit" and d.get("templates") == [])
    filt = urllib.parse.quote(json.dumps([{"pid": "status", "op": "is", "value": "offen"}]))
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rD}/rows?filters={filt}&limit=1&offset=1")
    ok(f"B: Filter + Blaettern serverseitig ({s}, matched {d.get('matched')}, {len(d.get('rows', []))})",
       s == 200 and d.get("matched") == 2 and len(d["rows"]) == 1 and d.get("total") == 5)
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rD}/rows?group=status")
    ok(f"B: Gruppen (Ansicht 2c) ({s})", s == 200 and {g["id"] for g in d.get("groups", [])} >= {"offen", "fertig"})
    r0 = ref(pa, rows[0])
    s, d = api("b", "fb-bert", "PATCH", f"/api/notes/{r0}", {"props": {"status": "offen", "zahl": 42, "kunde": [ref(pa, k1)]}})
    ok(f"B: Zeilenwerte + Relation bearbeitet ({s})", s == 200)
    got = json.loads(sql("a", "SELECT props FROM note WHERE id=?", (rows[0],))[0][0])
    ok(f"A: Werte gespeichert, Relation mit echter ID ({got})", got.get("zahl") == 42 and got.get("status") == "offen" and got.get("kunde") == [k1])
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{r0}")
    ok(f"B: Zeile lesen zeigt Relation als negative ID ({d.get('props')})", s == 200 and d["props"].get("kunde") == [ref(pa, k1)])
    s, d = api("b", "fb-bert", "PATCH", f"/api/notes/{r0}", {"props": {"kunde": [12345]}})
    got = json.loads(sql("a", "SELECT props FROM note WHERE id=?", (rows[0],))[0][0])
    ok(f"B: Relation auf eigene (B-)Seite faellt weg ({s}, {got.get('kunde')})", s == 200 and "kunde" not in got)
    sch = api("b", "fb-bert", "GET", f"/api/notes/{rD}")[1]["schema"]
    sch["props"].append({"id": "notiz", "name": "Notiz", "type": "text"})
    s, d = api("b", "fb-bert", "PATCH", f"/api/notes/{rD}", {"schema": sch})
    sa = json.loads(sql("a", "SELECT schema FROM note WHERE id=?", (D,))[0][0])
    rel_a = next(x for x in sa["props"] if x["type"] == "relation")
    ok(f"B: Spalte hinzugefuegt, Relation bleibt auf D2 ({s})", s == 200 and any(x["id"] == "notiz" for x in sa["props"]) and rel_a["target"] == D2)
    s, d = api("b", "fb-bert", "POST", "/api/notes", {"title": "Neue Aufgabe von B", "parent_id": rD, "props": {"status": "offen", "notiz": "hallo"}})
    nr = d.get("id")
    ok(f"B: Zeile angelegt (negative ID) ({s}, {nr})", s == 200 and isinstance(nr, int) and nr < 0)
    rr = sql("a", "SELECT uid, parent_id, created_by, props FROM note WHERE title='Neue Aufgabe von B'")
    ok(f"A: Zeile gehoert Anna, angelegt von bert@B ({rr[0][:3] if rr else rr})", rr and rr[0][0] == "fa-anna" and rr[0][1] == D
       and rr[0][2] == f"@bert@{HB}" and json.loads(rr[0][3]).get("notiz") == "hallo")
    s, d = api("b", "fb-anna", "PATCH", f"/api/notes/{r0}", {"props": {"zahl": 1}})
    ok(f"B: Anna-B (Lesen) darf Werte nicht aendern ({s})", s == 403)
    s, d = api("b", "fb-anna", "GET", f"/api/notes/{rD}/rows")
    ok(f"B: Anna-B (Lesen) sieht die Zeilen, Rolle view ({s})", s == 200 and d.get("role") == "view" and len(d["rows"]) == 6)
    s, d = api("b", "fb-anna", "POST", "/api/notes", {"title": "verboten", "parent_id": rD})
    ok(f"B: Anna-B (Lesen) darf keine Zeile anlegen ({s})", s == 403)
    return nr


def part_subpages(A, B, pa, pb, P, C, Q, D, D2, k1, rows):
    rP, rC, rQ = ref(pa, P), ref(pa, C), ref(pa, Q)
    s, d = api("b", "fb-bert", "POST", "/api/notes", {"title": "Unterseite von B", "parent_id": rP})
    sub = d.get("id")
    ok(f"B: Unterseite unter Projekt angelegt ({s})", s == 200 and sub and sub < 0)
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{sub}")
    ok(f"B: Unterseite oeffnen ({s}, parent {d.get('parent_id')})", s == 200 and d.get("parent_id") == rP and d.get("role") == "edit")
    s, d = api("b", "fb-bert", "POST", f"/api/notes/{sub}/move", {"parent_id": rC, "index": 0})
    ok(f"B: Unterseite in Kapitel verschoben (gleiche Freigaben) ({s})", s == 200 and d.get("parent_id") == rC)
    ok("A: Elternseite jetzt Kapitel", sql("a", "SELECT parent_id FROM note WHERE title='Unterseite von B'")[0][0] == C)
    s, d = api("b", "fb-bert", "POST", f"/api/notes/{sub}/move", {"parent_id": rQ, "index": 0})
    ok(f"B: in Bereich mit anderen Freigaben verschieben -> 403 ({s})", s == 403)
    s, d = api("b", "fb-bert", "POST", f"/api/notes/{sub}/move", {"parent_id": None, "index": 0})
    ok(f"B: aus dem geteilten Bereich hinaus -> 403 ({s})", s == 403)
    s, d = api("b", "fb-bert", "POST", f"/api/notes/{sub}/move", {"parent_id": 77, "index": 0})
    ok(f"B: unter eine eigene (B-)Seite -> 403 ({s})", s == 403)
    s, d = api("b", "fb-anna", "POST", "/api/notes", {"title": "x", "parent_id": rP})
    ok(f"B: Anna-B (Lesen) keine Unterseite ({s})", s == 403)
    s, d = api("b", "fb-anna", "POST", f"/api/notes/{sub}/move", {"parent_id": rP, "index": 0})
    ok(f"B: Anna-B (Lesen) verschiebt nicht ({s})", s == 403)
    s, d = api("b", "fb-bert", "DELETE", f"/api/notes/{sub}")
    ok(f"B: Loeschen bleibt dem Eigentuemer ({s})", s in (403, 404, 405))
    # lokal (Regel gilt auch innerhalb einer Instanz): Carl (Bearbeiten) verschiebt im geteilten Baum
    s, _ = api("a", "fa-anna", "PUT", f"/api/notes/{P}/shares", {"type": "user", "target": "fa-carl", "role": "edit"})
    loc = api("a", "fa-carl", "POST", "/api/notes", {"title": "Carls Seite", "parent_id": P})[1]["id"]
    s1 = api("a", "fa-carl", "POST", f"/api/notes/{loc}/move", {"parent_id": C, "index": 0})[0]
    s2 = api("a", "fa-carl", "POST", f"/api/notes/{loc}/move", {"parent_id": Q, "index": 0})[0]
    s3 = api("a", "fa-carl", "POST", f"/api/notes/{loc}/move", {"parent_id": None, "index": 0})[0]
    ok(f"A lokal: Bearbeiter verschiebt im Bereich (200), nicht in anders geteilte Bereiche / hinaus (403) ({s1}, {s2}, {s3})",
       s1 == 200 and s2 == 403 and s3 == 403)
    # Pruefer: gleiche Freigaben, aber neue Stelle "mit Unterseiten" veroeffentlicht -> nur der Eigentuemer (sonst oeffentlich)
    loc2 = api("a", "fa-carl", "POST", "/api/notes", {"title": "Carls Entwurf", "parent_id": P})[1]["id"]
    sp = api("a", "fa-anna", "GET", "/api/account/public-name")[1] or {}
    if not sp.get("name"):
        api("a", "fa-anna", "PUT", "/api/account/public-name", {"name": "fa-anna-pub"})
    s0, _ = api("a", "fa-anna", "PUT", f"/api/notes/{C}/publish", {"children": True})
    s4 = api("a", "fa-carl", "POST", f"/api/notes/{loc2}/move", {"parent_id": C, "index": 0})[0]
    s5 = api("a", "fa-anna", "POST", f"/api/notes/{loc2}/move", {"parent_id": C, "index": 0})[0]
    s6 = api("a", "fa-carl", "POST", f"/api/notes/{loc2}/move", {"parent_id": P, "index": 0})[0]
    ok(f"A lokal: Bearbeiter verschiebt nicht in/aus Veroeffentlichung mit Unterseiten ({s0}: {s4}, {s6}; Eigentuemer {s5})",
       s0 == 200 and s4 == 403 and s5 == 200 and s6 == 403)
    api("a", "fa-anna", "DELETE", f"/api/notes/{C}/publish")
    api("a", "fa-anna", "DELETE", f"/api/notes/{P}/shares?type=user&target=fa-carl")
    return sub


def part_upload(A, B, pa, pb, P, C, Q, D, D2, k1, rows):
    rP = ref(pa, P)
    img = F2.png(40, 30, (200, 30, 30))
    s, d = upload("b", "fb-bert", rP, img)
    url = (d.get("file") or {}).get("url") or ""
    ok(f"B: Bild in fremde Seite hochgeladen ({s}, {url[:40]})", s == 200 and url.startswith(f"/api/fed/files/{rP}/"))
    fid = url.rsplit("/", 1)[-1]
    row = sql("a", "SELECT uid, fed_by, fed_note FROM file WHERE id=?", (fid,))
    ok(f"A: Datei gehoert Anna, hochgeladen von bert@B fuer P ({row})", row and row[0] == ["fa-anna", f"@bert@{HB}", P])
    s, h, data = get_raw("b", "fb-bert", url)
    ok(f"B: Vorschau fuer die hochladende Person sofort ({s}, {h.get('content-type')})", s == 200 and h.get("content-type") == "image/png" and len(data) > 50)
    s, h, data = get_raw("b", "fb-anna", url)
    ok(f"B: andere Person sieht die Datei erst nach dem Speichern ({s})", s == 404)
    body = T.blocks(T.P("Mit Bild", "b1"), {"id": "img1", "type": "image", "data": {"file": {"url": url}, "caption": "rot"}})
    s, d = api("b", "fb-bert", "PATCH", f"/api/notes/{rP}", {"body": body, "format": "blocks"})
    ok(f"B: Seite mit Bild gespeichert ({s})", s == 200)
    ok("A: Inhalt verweist auf /files/<id>", f"/files/{fid}" in sql("a", "SELECT body FROM note WHERE id=?", (P,))[0][0])
    s, h, data = get_raw("b", "fb-anna", url)
    ok(f"B: jetzt sieht auch Anna-B (Lesen) das Bild ({s})", s == 200)
    s, d = upload("b", "fb-anna", rP, img)
    ok(f"B: Anna-B (Lesen) darf nicht hochladen ({s})", s == 403)
    big = os.urandom(600 * 1024)
    s1, d1 = upload("b", "fb-bert", rP, big, "a.bin", "file", "application/octet-stream")
    s2, d2 = upload("b", "fb-bert", rP, big, "b.bin", "file", "application/octet-stream")
    ok(f"B: Grenze je fremder Person (1 MB): erste Datei ok, zweite 507 ({s1}, {s2}, {d2.get('error', '')[:50]})", s1 == 200 and s2 == 507)
    s, d = upload("a", "fa-anna", P, big, "c.bin", "file", "application/octet-stream")
    ok(f"A: Eigentuemerin selbst nicht von der Grenze fuer Fremde betroffen ({s})", s == 200)
    own = api("b", "fb-bert", "POST", "/api/notes", {"title": "Berts eigene"})[1]["id"]
    burl = T.upload("b", "fb-bert")
    s, d = api("b", "fb-bert", "POST", "/api/files/copy", {"url": burl, "note": rP})
    ok(f"B: eigenes Bild in die fremde Seite kopiert (hochgeladen) ({s}, {str(d.get('url'))[:30]})", s == 200 and str(d.get("url", "")).startswith(f"/api/fed/files/{rP}/"))
    s, d = api("b", "fb-bert", "POST", "/api/files/copy", {"url": url, "note": rP})
    ok(f"B: Bild der fremden Seite dort kopiert ({s})", s == 200 and str(d.get("url", "")).startswith(f"/api/fed/files/{rP}/"))
    s, d = api("b", "fb-bert", "PATCH", f"/api/notes/{rP}", {"icon": url})
    ok(f"B: Bild der Seite als Symbol ({s})", s == 200 and sql("a", "SELECT icon FROM note WHERE id=?", (P,))[0][0] == f"/files/{fid}")
    api("b", "fb-bert", "DELETE", f"/api/notes/{own}")


def part_versions(A, B, pa, pb, P, C, Q, D, D2, k1, rows):
    rC = ref(pa, C)
    api("a", "fa-anna", "PATCH", f"/api/notes/{C}", {"body": T.blocks(T.P("Fassung eins", "v1")), "format": "blocks"})
    sql("a", "UPDATE note_version SET created=created-7200")
    api("a", "fa-anna", "PATCH", f"/api/notes/{C}", {"body": T.blocks(T.P("Fassung zwei", "v1")), "format": "blocks", "overwrite": True})
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rC}/versions")
    ok(f"B (Bearbeiten): Versionen der fremden Seite ({s}, {len(d) if isinstance(d, list) else d})", s == 200 and isinstance(d, list) and len(d) >= 1)
    vid = next((v["id"] for v in d if isinstance(d, list)), None) if s == 200 else None
    s, dv = api("b", "fb-bert", "GET", f"/api/notes/{rC}/versions/{vid}")
    ok(f"B: Fassung lesen ({s}, {str(dv.get('text'))[:30]})", s == 200 and "Fassung" in dv.get("text", ""))
    s, d = api("b", "fb-anna", "GET", f"/api/notes/{rC}/versions")
    ok(f"B (Lesen): Versionen -> 403 ({s})", s == 403)
    s, d = api("b", "fb-anna", "POST", f"/api/notes/{rC}/versions/{vid}/restore")
    ok(f"B (Lesen): Wiederherstellen -> 403 ({s})", s == 403)
    s, d = api("b", "fb-bert", "POST", f"/api/notes/{rC}/versions/{vid}/restore")
    body = sql("a", "SELECT body, updated_by FROM note WHERE id=?", (C,))[0]
    ok(f"B (Bearbeiten): Fassung wiederhergestellt ({s}, {body[1]})", s == 200 and "Fassung eins" in body[0] and body[1] == f"@bert@{HB}")
    s, d = api("a", "fa-anna", "GET", f"/api/notes/{C}/versions")
    ok(f"A: vorheriger Stand blieb als Version erhalten ({len(d)})", s == 200 and any(v.get("by") == "me" for v in d))


def part_export(A, B, pa, pb, P, C, Q, D, D2, k1, rows):
    rP, rD = ref(pa, P), ref(pa, D)
    s, h, data = get_raw("b", "fb-anna", f"/api/notes/{rP}/export")
    ok(f"B (Lesen): Export als Markdown ({s}, {h.get('content-type')})", s == 200 and h.get("content-type", "").startswith("text/markdown")
       and b"# " in data and b"Projekt" in data)
    s, h, data = get_raw("b", "fb-bert", f"/api/notes/{rP}/export?sub=1")
    names = zipfile.ZipFile(io.BytesIO(data)).namelist() if s == 200 else []
    ok(f"B: Export mit Unterseiten als ZIP ({s}, {len(names)} Dateien)", s == 200 and h.get("content-type") == "application/zip"
       and any("Kapitel" in n for n in names) and any(n.startswith("Projekt/") or n == "Projekt.md" for n in names))
    s, h, data = get_raw("b", "fb-bert", f"/api/notes/{rD}/export")
    ok(f"B: Datenbank als Markdown-Tabelle ({s})", s == 200 and b"| " in data and b"Aufgabe" in data)


def part_live(A, B, pa, pb, P, C, Q, D, D2, k1, rows):
    rP, rD = ref(pa, P), ref(pa, D)
    sb = Stream("b", "fb-bert")
    sa = Stream("a", "fa-anna")
    time.sleep(0.5)
    s, d = api("b", "fb-bert", "POST", "/api/presence", {"note_id": rP, "editing": True})
    ok(f"B: Anwesenheit auf fremder Seite ({s}, {[u.get('name') for u in d.get('users', [])]})", s == 200 and any(u.get("me") for u in d.get("users", []))
       and isinstance(d.get("rev"), int))
    s, d = api("a", "fa-anna", "POST", "/api/presence", {"note_id": P})
    ok(f"A: Anna sieht Bert (B) auf der Seite ({[u.get('name') for u in d.get('users', [])]})",
       s == 200 and any(u.get("remote") and HB in u.get("name", "") and u.get("editing") for u in d.get("users", [])))
    mark = len(sb.ev)
    pr = sb.wait("presence", lambda x: x.get("note_id") == rP and any(not u.get("me") and "Anna" in (u.get("name") or "") for u in x.get("users", [])), mark)
    ok(f"B: Live: Anwesenheit mit Anna kommt an ({bool(pr)})", bool(pr))
    mark = len(sb.ev)
    api("a", "fa-anna", "PATCH", f"/api/notes/{P}", {"title": "Projekt (neu)"})
    ev = sb.wait("note.changed", lambda x: x.get("id") == rP, mark)
    ok(f"B: Live: note.changed fuer die fremde Seite, ohne Inhalt ({ev})", bool(ev) and isinstance(ev.get("rev"), int) and "body" not in ev
       and "title" not in ev and (ev.get("by") or {}).get("name"))
    api("b", "fb-bert", "POST", "/api/presence", {"note_id": rD})
    mark = len(sb.ev)
    api("a", "fa-anna", "PATCH", f"/api/notes/{rows[1]}", {"props": {"zahl": 7}})
    ev = sb.wait("note.changed", lambda x: x.get("id") == rD and x.get("row") == ref(pa, rows[1]), mark)
    ok(f"B: Live: Zeile der Datenbank geaendert ({ev})", bool(ev))
    mark = len(sb.ev)
    api("a", "fa-anna", "POST", f"/api/notes/{P}/comments", {"block_id": "b1", "body": "Hallo Bert"})
    ev = sb.wait("comments.changed", lambda x: x.get("note_id") == rP, mark)
    ok(f"B: Live: Kommentar-Zaehler ({ev})", bool(ev) and (ev.get("counts") or {}).get("open") >= 1)
    mark = len(sb.ev)
    api("a", "fa-anna", "POST", "/api/notes", {"title": "Ganz neu", "parent_id": C})
    ev = sb.wait("shared.changed", None, mark)
    ok(f"B: Live: Seitenbaum des geteilten Bereichs geaendert -> shared.changed ({ev is not None})", ev is not None)
    mark_a = len(sa.ev)
    api("b", "fb-bert", "PATCH", f"/api/notes/{rP}", {"title": "Projekt (von B)"})
    ev = sa.wait("note.changed", lambda x: x.get("id") == P and HB in ((x.get("by") or {}).get("name") or ""), mark_a)
    ok(f"A: Live: Anna sieht Berts Aenderung mit Herkunft ({ev})", bool(ev))
    # Abo-Ablauf: ohne weitere Meldung verschwindet Bert nach FED_WATCH_TTL aus der Anwesenheit, Ereignisse enden
    def nobody():
        d = api("a", "fa-anna", "POST", "/api/presence", {"note_id": P})[1]
        return "users" in d and not any(u.get("remote") for u in d["users"])        # (Antwort "skipped" zaehlt nicht)
    gone = wait(nobody, WATCH + 12, 1.5)
    ok(f"A: Abo laeuft ab (nach {WATCH} s ohne Meldung nicht mehr anwesend)", gone)
    mark = len(sb.ev)
    api("a", "fa-anna", "PATCH", f"/api/notes/{P}", {"title": "Projekt (nach Ablauf)"})
    time.sleep(2)
    late = [e for e in sb.ev[mark:] if e[0] == "note.changed" and e[1].get("id") == rP]
    ok(f"B: nach Ablauf keine Seiten-Ereignisse mehr ({late})", not late)
    s, d = api("b", "fb-bert", "POST", "/api/presence", {"note_id": rP, "leave": True})
    ok(f"B: Verlassen ({s})", s == 200)
    sb.close(); sa.close()


def part_rights(A, B, pa, pb, P, C, Q, D, D2, k1, rows):
    rQ = ref(pa, Q)
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rQ}")
    ok(f"B: Bert sieht Q mit hoechster Rolle (edit ueber Projekt, comment ueber Gruppe) ({d.get('role')})", s == 200 and d.get("role") == "edit")
    api("a", "fa-anna", "DELETE", f"/api/notes/{P}/shares?type=remote&target=" + f"@bert@{HB}".replace("@", "%40"))
    ok("B: Freigabe Projekt fuer Bert entzogen", wait(lambda: len(sql("b", "SELECT 1 FROM fed_inbound WHERE target='fb-bert'")) == 0, 10))
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rQ}")
    ok(f"B: Bert (ueber Gruppe fbteam) jetzt nur Kommentieren ({s}, {d.get('role')})", s == 200 and d.get("role") == "comment")
    s, d = api("b", "fb-bert", "PATCH", f"/api/notes/{rQ}", {"title": "x"})
    ok(f"B (Kommentieren): Titel aendern -> 403 ({s})", s == 403)
    s, d = api("b", "fb-bert", "POST", "/api/notes", {"title": "x", "parent_id": rQ})
    ok(f"B (Kommentieren): Unterseite -> 403 ({s})", s == 403)
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rQ}/versions")
    ok(f"B (Kommentieren): Versionen -> 403 ({s})", s == 403)
    s, d = api("b", "fb-bert", "POST", f"/api/notes/{rQ}/comments", {"block_id": "x", "body": "Kommentar ueber Gruppe"})
    ok(f"B (Kommentieren): Kommentar geht ({s})", s == 200)
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{ref(pa, D)}/rows")
    ok(f"B: Datenbank ausserhalb von Q nicht mehr sichtbar ({s})", s == 404)
    for p in ("shares", "publish", "backlinks"):
        s, d = api("b", "fb-bert", "GET", f"/api/notes/{rQ}/{p}")
        ok(f"B: {p} fuer fremde Seiten nicht verfuegbar ({s})", s == 404)


def part_gone(A, B, pa, pb, P):
    _, html = A.post({"action": "disconnect", "id": str(pa)})
    ok("A trennt die Verbindung", "getrennt" in T.flash(html))
    s, d = api("b", "fb-anna", "GET", f"/api/notes/{ref(pa, P)}/rows")
    ok(f"B: nach dem Trennen nichts mehr ({s})", s in (404, 502))


def part_compat(A, B, pa, pb):
    """B = aeltere Version (Protokoll 1): A erkennt es, neue Funktionen melden "noch nicht", Protokoll 1 laeuft."""
    ok("A: Gegenstelle ohne db/upload", wait(lambda: sql("a", "SELECT caps_at FROM fed_peer")[0][0] is not None, 20)
       and "db" not in sql("a", "SELECT caps FROM fed_peer")[0][0])
    P, C, Q, D, D2, k1, rows = setup(pa, pb)
    s, _ = api("a", "fa-anna", "PUT", f"/api/notes/{P}/shares", {"type": "remote", "target": f"@bert@{HB}", "role": "edit"})
    ok(f"A: an alte Instanz geteilt ({s})", s == 200)
    ok("B (alt): Freigabe angekommen", wait(lambda: len(sql("b", "SELECT 1 FROM fed_inbound")) >= 1, 15))
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{ref(pa, P)}")
    ok(f"B (alt): Seite lesen ueber Protokoll 1 ({s})", s == 200 and "Projekt" in d.get("title", ""))
    s, d = api("b", "fb-bert", "PATCH", f"/api/notes/{ref(pa, P)}", {"title": "Projekt (alt)"})
    ok(f"B (alt): Titel bearbeiten ueber Protokoll 1 ({s})", s == 200 and sql("a", "SELECT title FROM note WHERE id=?", (P,))[0][0] == "Projekt (alt)")
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{ref(pa, D)}")
    ok(f"B (alt): Datenbank weiter als Hinweis ({s})", s == 200)
    # Gegenrichtung: Bert (alt) teilt mit Anna; A (neu) als Heim
    bn = api("b", "fb-bert", "POST", "/api/notes", {"title": "Berts Seite"})[1]["id"]
    s, _ = api("b", "fb-bert", "PUT", f"/api/notes/{bn}/shares", {"type": "remote", "target": f"@anna@{HA}", "role": "edit"})
    ok("A: Freigabe von B (alt) angekommen", wait(lambda: len(sql("a", "SELECT 1 FROM fed_inbound")) >= 1, 15))
    r = ref(pb, bn)
    s, d = api("a", "fa-anna", "GET", f"/api/notes/{r}")
    ok(f"A: Seite von B (alt) lesen ({s})", s == 200)
    for name, (m, p, b) in {"Zeilen": ("GET", f"/api/notes/{r}/rows", None), "Versionen": ("GET", f"/api/notes/{r}/versions", None),
                            "Export": ("GET", f"/api/notes/{r}/export", None), "Unterseite": ("POST", "/api/notes", {"title": "x", "parent_id": r})}.items():
        s, d = api("a", "fa-anna", m, p, b)
        ok(f"A -> B (alt): {name} meldet verstaendlich 'noch nicht' ({s}, {str(d.get('error') if isinstance(d, dict) else d)[:60]})",
           s == 404 and HB in str(d.get("error", "")))
    s, d = upload("a", "fa-anna", r, F2.png())
    ok(f"A -> B (alt): Hochladen meldet 'noch nicht' ({s})", s == 404)
    s, d = api("a", "fa-anna", "POST", "/api/presence", {"note_id": r})
    ok(f"A -> B (alt): Anwesenheit ohne Fehler (nur ich, ohne Live) ({s})", s == 200 and d.get("live") is False)


def run():
    try:
        A, B, pa, pb = F2.main()
        if COMPAT:
            part_compat(A, B, pa, pb)
        else:
            P, C, Q, D, D2, k1, rows = part_share(A, B, pa, pb)
            part_db(A, B, pa, pb, P, C, Q, D, D2, k1, rows)
            part_subpages(A, B, pa, pb, P, C, Q, D, D2, k1, rows)
            part_upload(A, B, pa, pb, P, C, Q, D, D2, k1, rows)
            part_versions(A, B, pa, pb, P, C, Q, D, D2, k1, rows)
            part_export(A, B, pa, pb, P, C, Q, D, D2, k1, rows)
            part_live(A, B, pa, pb, P, C, Q, D, D2, k1, rows)
            part_rights(A, B, pa, pb, P, C, Q, D, D2, k1, rows)
            part_gone(A, B, pa, pb, P)
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
    res = T.res
    print(f"\nFOEDERATION-TEILEN {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    run()
