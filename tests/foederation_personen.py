"""2.5 Paket 1: Personen- und Datei-Eigenschaften ueber Servergrenzen (Faehigkeit dbx1) - zwei eigene Saga-Instanzen A und B
(Geruest wie tests/foederation2.py / foederation_formeln.py).

Anna (A) teilt die Datenbank "Aufgaben" (Person, Dateien) mit Bert auf B (Bearbeiten). Geprueft:
- Faehigkeit dbx1 im hello (remote.caps).
- B liest /rows: Anna als Handle von B? Nein - als Prinzipal '@anna@A' mit Anzeigename; Bert als eigenes Konto (Handle von
  B, me=true); kein Anmeldename von A in Antworten an B (Marker-Konten), keine Datei-Leckage, Datei ueber den Proxy.
- Auswahl /people ueber das Heim (Anna, Bert; nicht Carl ohne Freigabe), Bert traegt sich und Anna ein (PATCH ueber das
  Heim), A speichert Bert als Prinzipal, row_person bei A, Mitteilung db.assigned an Bert auf B (Ereignis), Filter "me"
  ueber das Heim, Gruppen nach Person.
- Datei: Bert laedt ueber das Heim hoch und setzt den Wert, A speichert /files/<id>, B sieht die Datei ueber den Proxy,
  nach GC bei A noch da.
- Heim ohne dbx1 (aeltere Saga): Personen-/Datei-Spalten als Text ohne Werte, unsupported; Schema zurueck -> Typen bleiben;
  Werte von B werden verworfen.
- person.renamed: Bert bekommt einen oeffentlichen Namen -> Wert bei A umgeschrieben.
Ports: FEDP_PORTS="a,a-admin,b,b-admin" (Standard 8250,8251,8252,8253). Raeumt auf (Datenbanken geloescht)."""
import json, os, sys, time
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foederation as T  # noqa: E402
import foederation2 as F2  # noqa: E402

pa_, aa_, pb_, ab_ = (int(x) for x in os.environ.get("FEDP_PORTS", "8250,8251,8252,8253").split(","))
T.I["a"].update(port=pa_, admin=aa_, db="saga_fppa", url=f"http://127.0.0.1:{pa_}", host=f"127.0.0.1:{pa_}", state="/tmp/st-b-people/fedp-a",
                allow=f"localhost:{pb_}")
T.I["b"].update(port=pb_, admin=ab_, db="saga_fppb", url=f"http://localhost:{pb_}", host=f"localhost:{pb_}", state="/tmp/st-b-people/fedp-b",
                allow=f"127.0.0.1:{pa_}")
HA, HB = T.I["a"]["host"], T.I["b"]["host"]
api, sql = T.api, T.sql


def ok(n, c, info=None):
    T.ok(n if c or info is None else f"{n} -> {str(info)[:500]}", c)


def ref(pid, rid):
    return -(rid * 1024 + pid)


def qs(**kw):
    return "?" + "&".join(f"{k}={quote(json.dumps(v) if isinstance(v, (list, dict)) else str(v))}" for k, v in kw.items())


def local_ref(k, uid):
    return T.py(k, f"from saga.core import local_ref\nprint(json.dumps(local_ref({uid!r})))")


def part(pa, pb):
    an = "fa-anna"
    bert_b = local_ref("b", "fb-bert")
    db = api("a", an, "POST", "/api/notes", {"title": "Aufgaben", "kind": "database"})[1]["id"]
    s, d = api("a", an, "PATCH", f"/api/notes/{db}", {"schema": {"props": [
        {"id": "wer", "name": "Zuständig", "type": "person"}, {"id": "anh", "name": "Anhänge", "type": "files"},
        {"id": "fx", "name": "Namen", "type": "formula", "expr": "prop(\"wer\").join(\", \")"}],
        "views": [{"id": "alle", "name": "Alle", "type": "table"}, {"id": "mir", "name": "Mir", "type": "table",
                                                                   "filters": [{"pid": "wer", "op": "me", "value": ""}]}]}})
    ok(f"A: Datenbank mit Person/Dateien ({s})", s == 200, d)
    r1 = api("a", an, "POST", "/api/notes", {"title": "Erste", "parent_id": db, "props": {"wer": ["fa-anna"]}})[1]["id"]
    r2 = api("a", an, "POST", "/api/notes", {"title": "Zweite", "parent_id": db})[1]["id"]
    s, _ = api("a", an, "PUT", f"/api/notes/{db}/shares", {"type": "remote", "target": f"@bert@{HB}", "role": "edit"})
    ok(f"A: Aufgaben mit Bert@B geteilt (Bearbeiten) ({s})", s == 200)
    ok("B: Freigabe angekommen", T.wait(lambda: len(sql("b", "SELECT 1 FROM fed_inbound")) >= 1, 20))
    rDB, rR1, rR2 = ref(pa, db), ref(pa, r1), ref(pa, r2)
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rDB}")
    ok(f"B: Faehigkeit dbx1 gemeldet ({s})", s == 200 and "dbx1" in (d.get("remote") or {}).get("caps", []), d.get("remote"))
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rDB}/rows")
    raw = json.dumps(d, ensure_ascii=False)
    by = {r["id"]: r for r in d.get("rows", [])}
    w = by[rR1]["props"].get("wer") or []
    ok(f"B: Anna als Prinzipal mit Anzeigename ({s})", s == 200 and w == [f"@anna@{HA}"] and "Anna Berg" in (d.get("people") or {}).get(w[0], {}).get("name", ""), (w, d.get("people")))
    ok("B: kein Anmeldename von A (fa-anna) in der Antwort", "fa-anna" not in raw, raw[:400])
    ok("B: Formel mit Anzeigename (rechnet der Eigentuemer)", by[rR1]["props"].get("fx") == "Anna Berg", by[rR1]["props"])
    # Auswahl ueber das Heim
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rDB}/people")
    refs = {x["ref"]: x for x in d.get("people", [])}
    ok(f"B: Auswahl ueber das Heim: Anna (fremd) und Bert (ich, Handle von B) ({s})", s == 200 and set(refs) == {f"@anna@{HA}", bert_b}
       and refs[bert_b]["me"] is True and refs[f"@anna@{HA}"]["remote"] is True, d)
    ok("B: Auswahl ohne Anmeldenamen von A", "fa-" not in json.dumps(d), d)
    # Bert traegt sich und Anna ein
    s, d = api("b", "fb-bert", "PATCH", f"/api/notes/{rR2}", {"props": {"wer": [bert_b, f"@anna@{HA}"]}})
    ok(f"B: PATCH ueber das Heim (sich + Anna) ({s})", s == 200 and (d.get("values") or {}).get("wer") == [bert_b, f"@anna@{HA}"], d)
    st = json.loads(sql("a", "SELECT props FROM note WHERE id=?", (r2,))[0][0])
    ok("A: Bert als Prinzipal gespeichert, Anna als Konto", st.get("wer") == [f"@bert@{HB}", "fa-anna"], st)
    rp = sorted(x[0] for x in sql("a", "SELECT person FROM row_person WHERE note_id=?", (r2,)))
    ok("A: row_person mit Prinzipal", rp == sorted([f"@bert@{HB}", "fa-anna"]), rp)
    s, d = api("a", an, "GET", f"/api/notes/{db}/rows")
    pm = d.get("people") or {}
    ok("A: Bert mit Herkunft im Namen", "Bert" in pm.get(f"@bert@{HB}", {}).get("name", "") and HB in pm[f"@bert@{HB}"]["name"], pm)
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rDB}/rows" + qs(view="mir"))
    ok("B: Filter 'mir' ueber das Heim", s == 200 and [r["id"] for r in d.get("rows", [])] == [rR2], d.get("rows"))
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rDB}/rows" + qs(group="wer"))
    gv = [g.get("value") for g in d.get("groups") or []]
    ok("B: Gruppen nach Person (Kennungen von B)", s == 200 and [f"@anna@{HA}"] in gv and [bert_b, f"@anna@{HA}"] in gv, gv)
    s, d = api("b", "fb-bert", "PATCH", f"/api/notes/{rR1}", {"props": {"wer": ["@fremd@nirgendwo.example"]}})
    ok(f"B: Person ohne Zugriff -> 400 ueber das Heim ({s})", s == 400, d)
    # Mitteilung an Bert auf B
    api("a", an, "PATCH", f"/api/notes/{r1}", {"props": {"wer": ["fa-anna", f"@bert@{HB}"]}})
    ok("B: db.assigned angekommen", T.wait(lambda: any(x["kind"] == "db.assigned" for x in
                                                       api("b", "fb-bert", "GET", "/api/notifications")[1].get("items", [])), 20))
    s, d = api("b", "fb-bert", "GET", "/api/notifications")
    it = next((x for x in d.get("items", []) if x["kind"] == "db.assigned"), {})
    ok("B: Mitteilung mit Name und Titel, ohne Anmeldename", "Anna" in it.get("text", "") and it.get("title") == "Erste" and "fa-anna" not in json.dumps(d), it)

    # --- Dateien -----------------------------------------------------------------------------------------------------------
    s, _, up = T.hreq("b", "POST", f"/api/files?note={rR2}", raw=(b"--fp\r\nContent-Disposition: form-data; name=\"file\"; filename=\"plan.txt\"\r\n"
                                                                  b"Content-Type: text/plain\r\n\r\nPlan von Bert\r\n--fp--\r\n"),
                      hdr={"X-Saga": "1", "Content-Type": "multipart/form-data; boundary=fp"}, cookie=T.ck("b", "fb-bert"))
    f = (T.jd(up).get("file") or {})
    ok(f"B: Upload ueber das Heim ({s})", s == 200 and f.get("url", "").startswith(f"/api/fed/files/{rR2}/"), up)
    s, d = api("b", "fb-bert", "PATCH", f"/api/notes/{rR2}", {"props": {"anh": [{"url": f["url"], "name": "plan.txt"}]}})
    ok(f"B: Datei-Wert gesetzt ({s})", s == 200, d)
    st = json.loads(sql("a", "SELECT props FROM note WHERE id=?", (r2,))[0][0])
    fid = f["url"].rsplit("/", 1)[-1]
    ok("A: als /files/<id> gespeichert", st.get("anh") == [{"url": f"/files/{fid}", "name": "plan.txt"}], st)
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rDB}/rows")
    v = next(r for r in d["rows"] if r["id"] == rR2)["props"].get("anh") or []
    ok("B: Datei-Wert ueber den Proxy, mit Groesse", v and v[0]["url"] == f"/api/fed/files/{rR2}/{fid}" and v[0].get("size") == 13, v)
    s, _, body = T.hreq("b", "GET", v[0]["url"] if v else "/", cookie=T.ck("b", "fb-bert"))
    ok(f"B: Datei ueber den Proxy abrufbar ({s})", s == 200 and body == b"Plan von Bert", (s, body[:50]))
    T.py("a", "from saga.files import files_gc\nfrom db import connect\n"
              "with connect() as c, c:\n    c.execute(\"UPDATE file SET created=created-200000\")\nprint(json.dumps(files_gc()))")
    ok("A: Datei nach GC noch da (steht nur in props)", bool(sql("a", "SELECT 1 FROM file WHERE id=?", (fid,))))
    s, _, body = T.hreq("b", "GET", v[0]["url"] if v else "/", cookie=T.ck("b", "fb-bert"))
    ok(f"B: nach GC weiter abrufbar ({s})", s == 200)

    # --- Heim ohne dbx1 (aeltere Saga) --------------------------------------------------------------------------------------
    capsa = json.loads(sql("a", "SELECT caps FROM fed_peer")[0][0])
    sql("a", "UPDATE fed_peer SET caps=?, caps_at=? WHERE id=?", (json.dumps([x for x in capsa if x != "dbx1"]), time.time(), pa))
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rDB}/rows")
    sch = {p["id"]: p for p in d.get("schema", {}).get("props", [])}
    by = {r["id"]: r for r in d.get("rows", [])}
    ok(f"Alt: Spalten als Text mit fx_orig, keine Werte, unsupported ({s})", s == 200 and sch["wer"]["type"] == "text"
       and sch["wer"]["fx_orig"]["type"] == "person" and sch["anh"]["fx_orig"]["type"] == "files"
       and "wer" not in by[rR2]["props"] and "anh" not in by[rR2]["props"] and set(d.get("unsupported") or []) == {"person", "files"}, (sch, by.get(rR2)))
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rR2}")
    ok("Alt: Einzelzeile ohne Personen-/Datei-Werte", s == 200 and "wer" not in d.get("props", {}) and "anh" not in d.get("props", {}), d.get("props"))
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rDB}/rows?limit=1")
    old = d["schema"]
    s, d = api("b", "fb-bert", "PATCH", f"/api/notes/{rDB}", {"schema": old})
    s2, d2 = api("a", an, "GET", f"/api/notes/{db}/rows")
    sa = {p["id"]: p for p in d2["schema"]["props"]}
    ok(f"Alt: Schema zurueck -> Typen bei A unveraendert ({s})", s == 200 and sa["wer"]["type"] == "person" and sa["anh"]["type"] == "files", sa)
    ok("Alt: Werte bei A unveraendert", json.loads(sql("a", "SELECT props FROM note WHERE id=?", (r2,))[0][0]).get("wer") == [f"@bert@{HB}", "fa-anna"])
    s, d = api("b", "fb-bert", "PATCH", f"/api/notes/{rR1}", {"props": {"wer": "Bert als Text"}})
    ok(f"Alt: Werte von B fuer Personen-Spalte verworfen ({s})", json.loads(sql("a", "SELECT props FROM note WHERE id=?", (r1,))[0][0]).get("wer")
       == ["fa-anna", f"@bert@{HB}"], d)
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rDB}/people")
    ok(f"Alt: Auswahl -> 403/404 ({s})", s in (403, 404), d)
    sql("a", "UPDATE fed_peer SET caps=?, caps_at=? WHERE id=?", (json.dumps(capsa), time.time(), pa))

    # --- Umbenennen ------------------------------------------------------------------------------------------------------
    s, d = api("b", "fb-bert", "PUT", "/api/account/public-name", {"name": "bertneu"})
    ok(f"B: Bert benennt sich um ({s})", s == 200, d)
    ok("A: Personen-Wert auf neuen Namen umgeschrieben",
       T.wait(lambda: json.loads(sql("a", "SELECT props FROM note WHERE id=?", (r2,))[0][0]).get("wer") == [f"@bertneu@{HB}", "fa-anna"], 20),
       sql("a", "SELECT props FROM note WHERE id=?", (r2,)))
    ok("A: row_person umgeschrieben", sorted(x[0] for x in sql("a", "SELECT person FROM row_person WHERE note_id=?", (r2,))) ==
       sorted([f"@bertneu@{HB}", "fa-anna"]))


def run():
    try:
        A, B, pa, pb = F2.main()
        part(pa, pb)
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
    print(f"\nFOEDERATION-PERSONEN {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    run()
