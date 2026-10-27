"""2.5 Paket 1: Formeln und Rollups ueber Servergrenzen - zwei eigene Saga-Instanzen A und B (Geruest wie tests/foederation2.py).

Anna (A) hat die Datenbanken "Aufgaben" (Formeln, Rollup ueber eine Relation) und "Projekte" (Ziel der Relation); sie
teilt NUR "Aufgaben" mit Bert auf B. Geprueft:
- Faehigkeit formula1 im hello (remote.caps).
- B liest /rows der fremden Datenbank: Formel-Werte fertig vom Eigentuemer, rtype im Schema, fx_err je Zeile; Rollup auf
  die nicht geteilte Ziel-Datenbank ohne Wert und ohne Titel (Leck-Test ueber Servergrenzen).
- Filter/Sortierung/Gruppen nach Formel ueber das Heim (groups mit value), PATCH-Antwort mit calc, Einzelzeile.
- Formel-Editor ueber das Heim (POST /api/notes/{-db}/formula/check): IDs <-> Namen, Vorschau, Fehler mit Position.
- Bearbeitende Person aendert eine Formel (Schema-PATCH ueber das Heim) -> A rechnet neu.
- Gegenstelle ohne formula1 (aeltere Saga): A liefert Formel/Rollup als Text-Spalte (fx_orig), B speichert das Schema
  zurueck -> Formeln bleiben bei A unveraendert. Ohne formula1 beim Eigentuemer: Editor -> 404 mit Hinweis.
Ports: FEDFX_PORTS="a,a-admin,b,b-admin" (Standard 8112,8113,8114,8115). Raeumt auf (Datenbanken geloescht)."""
import json, os, sys, time
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foederation as T  # noqa: E402
import foederation2 as F2  # noqa: E402

pa_, aa_, pb_, ab_ = (int(x) for x in os.environ.get("FEDFX_PORTS", "8112,8113,8114,8115").split(","))
T.I["a"].update(port=pa_, admin=aa_, db="saga_ffxa", url=f"http://127.0.0.1:{pa_}", host=f"127.0.0.1:{pa_}", state="/tmp/fedfx-a",
                allow=f"localhost:{pb_}")
T.I["b"].update(port=pb_, admin=ab_, db="saga_ffxb", url=f"http://localhost:{pb_}", host=f"localhost:{pb_}", state="/tmp/fedfx-b",
                allow=f"127.0.0.1:{pa_}")
HA, HB = T.I["a"]["host"], T.I["b"]["host"]
api, sql = T.api, T.sql


def ok(n, c, info=None):
    T.ok(n if c or info is None else f"{n} -> {str(info)[:400]}", c)


def ref(pid, rid):
    return -(rid * 1024 + pid)


def qs(**kw):
    return "?" + "&".join(f"{k}={quote(json.dumps(v) if isinstance(v, (list, dict)) else str(v))}" for k, v in kw.items())


def part(pa, pb):
    an = "fa-anna"
    proj = api("a", an, "POST", "/api/notes", {"title": "Geheimprojekte", "kind": "database"})[1]["id"]
    api("a", an, "PATCH", f"/api/notes/{proj}", {"schema": {"props": [{"id": "budget", "name": "Budget", "type": "number"}]}})
    p1 = api("a", an, "POST", "/api/notes", {"title": "Projekt Zebra", "parent_id": proj, "props": {"budget": 70}})[1]["id"]
    db = api("a", an, "POST", "/api/notes", {"title": "Aufgaben", "kind": "database"})[1]["id"]
    s, d = api("a", an, "PATCH", f"/api/notes/{db}", {"schema": {"props": [
        {"id": "menge", "name": "Menge", "type": "number"}, {"id": "d", "name": "Datum", "type": "date"},
        {"id": "proj", "name": "Projekt", "type": "relation", "target": proj},
        {"id": "dop", "name": "Doppelt", "type": "formula", "expr": "prop(\"menge\") * 2"},
        {"id": "tag", "name": "Tag", "type": "formula", "expr": "formatDate(prop(\"d\"), \"dddd\")"},
        {"id": "inv", "name": "Kehrwert", "type": "formula", "expr": "1 / prop(\"menge\")"},
        {"id": "pb", "name": "Budget", "type": "rollup", "rel": "proj", "target": "budget", "fn": "sum"},
        {"id": "pn", "name": "Projekte", "type": "rollup", "rel": "proj", "target": "title", "fn": "show"}]}})
    ok(f"A: Datenbank mit Formeln/Rollups ({s})", s == 200, d)
    r1 = api("a", an, "POST", "/api/notes", {"title": "Montag", "parent_id": db, "props": {"menge": 2, "d": "2026-10-26", "proj": [p1]}})[1]["id"]
    r2 = api("a", an, "POST", "/api/notes", {"title": "Null", "parent_id": db, "props": {"menge": 0, "d": "2026-10-28"}})[1]["id"]
    s, d = api("a", an, "GET", f"/api/notes/{db}/rows")
    ok("A selbst: Rollup mit Wert (Kontrolle)", next(r for r in d["rows"] if r["id"] == r1)["props"].get("pb") == 70, d)
    s, _ = api("a", an, "PUT", f"/api/notes/{db}/shares", {"type": "remote", "target": f"@bert@{HB}", "role": "edit"})
    ok(f"A: Aufgaben mit Bert@B geteilt (Bearbeiten) ({s})", s == 200)
    ok("B: Freigabe angekommen", T.wait(lambda: len(sql("b", "SELECT 1 FROM fed_inbound")) >= 1, 20))
    rDB, rR1, rR2 = ref(pa, db), ref(pa, r1), ref(pa, r2)
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rDB}")
    ok(f"B: Datenbank lesen, Faehigkeit formula1 gemeldet ({s})", s == 200 and "formula1" in (d.get("remote") or {}).get("caps", []), d.get("remote"))
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rDB}/rows?tz=Europe/Berlin")
    raw = json.dumps(d, ensure_ascii=False)
    by = {r["id"]: r for r in d.get("rows", [])}
    sch = {p["id"]: p for p in d.get("schema", {}).get("props", [])}
    ok(f"B: Formel-Werte vom Eigentuemer ({s})", s == 200 and by[rR1]["props"].get("dop") == 4 and by[rR1]["props"].get("tag") == "Montag", by.get(rR1))
    ok("B: rtype und Ausdruck im Schema (Relation-Ziel als negative ID)", sch["dop"].get("rtype") == "number" and sch["tag"].get("rtype") == "text"
       and sch["dop"].get("expr") == "prop(\"menge\") * 2" and sch["proj"]["target"] < 0, sch)
    ok("B: fx_err je Zeile kommt an", by[rR2].get("fx_err") == {"inv": "num"} and "fx_err" not in by[rR1], by.get(rR2))
    ok("B: Leck-Test - Rollup auf nicht geteilte Ziel-DB ohne Wert/Titel", "pb" not in by[rR1]["props"] and "pn" not in by[rR1]["props"]
       and "Zebra" not in raw and "70" not in json.dumps(by[rR1]["props"]), by[rR1]["props"])
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rDB}/rows" + qs(group="tag", sorts=[{"pid": "dop", "dir": "desc"}], tz="Europe/Berlin"))
    ok(f"B: Gruppen nach Formel mit value ueber das Heim ({s})", s == 200 and [g.get("value") for g in d.get("groups", [])][1:] == ["Montag", "Mittwoch"]
       and d["groups"][0] == {"id": None, "count": 0}, d.get("groups"))
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rDB}/rows" + qs(filters=[{"pid": "dop", "op": "gt", "value": "1"}]))
    ok("B: Filter auf Formel ueber das Heim", s == 200 and [r["id"] for r in d["rows"]] == [rR1], d)
    s, d = api("b", "fb-bert", "PATCH", f"/api/notes/{rR1}", {"props": {"menge": 5}})
    ok(f"B: PATCH einer Zeile -> calc mit neuen Werten, ohne Rollup ({s})", s == 200 and d.get("calc", {}).get("dop") == 10
       and d["calc"].get("pb") is None and d.get("fx_err") == {}, d)
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rR1}")
    ok(f"B: Einzelzeile mit Formelwert ({s})", s == 200 and d.get("props", {}).get("dop") == 10 and "pb" not in d.get("props", {}), d.get("props"))
    # Formel-Editor ueber das Heim
    s, d = api("b", "fb-bert", "POST", f"/api/notes/{rDB}/formula/check", {"expr": "prop(\"Menge\") + 1", "pid": "neu"})
    ok(f"B: formula/check ueber das Heim: Namen -> IDs, Vorschau mit negativen IDs ({s})", s == 200 and d.get("ok") and d["expr"] == "prop(\"menge\") + 1"
       and d["rtype"] == "number" and all(x["id"] < 0 for x in d["preview"]) and {x["value"] for x in d["preview"]} == {6, 1}, d)
    s, d = api("b", "fb-bert", "POST", f"/api/notes/{rDB}/formula/check", {"expr": "prop(\"Menge\") +", "pid": "neu"})
    ok("B: formula/check Fehler mit Position", s == 200 and d.get("ok") is False and d["error"]["pos"] == 15, d)
    s, d = api("b", "fb-bert", "POST", f"/api/notes/{rDB}/formula/check", {"expr": "prop(\"Projekte\")", "pid": "neu"})
    ok("B: Vorschau ueber Rollup ohne Werte (Leck-Test)", s == 200 and all(x["value"] is None for x in d.get("preview", [])), d)
    # Bert aendert eine Formel (Rolle Bearbeiten) ueber das Heim
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rDB}/rows?limit=1")
    sch = d["schema"]
    next(p for p in sch["props"] if p["id"] == "dop")["expr"] = "prop(\"menge\") * 3"
    s, d = api("b", "fb-bert", "PATCH", f"/api/notes/{rDB}", {"schema": sch})
    ok(f"B: Formel aendern (Schema-PATCH ueber das Heim) ({s})", s == 200, d)
    s, d = api("a", an, "GET", f"/api/notes/{db}/rows")
    a1 = next(r for r in d["rows"] if r["id"] == r1)
    ok("A: neue Formel gilt, Relation/Rollup unveraendert", a1["props"].get("dop") == 15 and a1["props"].get("pb") == 70
       and next(p for p in d["schema"]["props"] if p["id"] == "proj")["target"] == proj, a1)
    bad = json.loads(json.dumps(sch))
    next(p for p in bad["props"] if p["id"] == "dop")["expr"] = "prop(\"menge\") *"
    s, d = api("b", "fb-bert", "PATCH", f"/api/notes/{rDB}", {"schema": bad})
    ok(f"B: fehlerhafte Formel ueber das Heim -> 400 mit Meldung ({s})", s == 400 and "Zeichen" in str(d), d)

    # Gegenstelle ohne formula1 (aeltere Saga): A meldet B ohne formula1 -> Text-Spalten mit fx_orig
    capsa = json.loads(sql("a", "SELECT caps FROM fed_peer")[0][0])
    sql("a", "UPDATE fed_peer SET caps=?, caps_at=? WHERE id=?", (json.dumps([x for x in capsa if x != "formula1"]), time.time(), pa))
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rDB}/rows")
    sch = {p["id"]: p for p in d.get("schema", {}).get("props", [])}
    by = {r["id"]: r for r in d.get("rows", [])}
    ok(f"Alt: Formel als Text-Spalte mit fx_orig, Wert als Text ({s})", s == 200 and sch["dop"]["type"] == "text" and sch["dop"]["fx_orig"]["type"] == "formula"
       and by[rR1]["props"].get("dop") == "15" and "fx_err" not in by[rR2], (sch.get("dop"), by.get(rR1)))
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rR1}")
    ok("Alt: Einzelzeile mit Text-Wert", s == 200 and d.get("props", {}).get("dop") == "15", d.get("props"))
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rDB}/rows?limit=1")
    old_schema = d["schema"]
    old_schema["props"].append({"id": "neu", "name": "Neu", "type": "text"})
    s, d = api("b", "fb-bert", "PATCH", f"/api/notes/{rDB}", {"schema": old_schema})
    s2, d2 = api("a", an, "GET", f"/api/notes/{db}/rows")
    sa = {p["id"]: p for p in d2["schema"]["props"]}
    ok(f"Alt: Schema zurueckgeschrieben -> Formeln/Rollups bei A unveraendert ({s})", s == 200 and sa["dop"]["type"] == "formula"
       and sa["dop"]["expr"] == "prop(\"menge\") * 3" and sa["pb"]["type"] == "rollup" and sa["pb"]["rel"] == "proj" and "neu" in sa, sa)
    sql("a", "UPDATE fed_peer SET caps=?, caps_at=? WHERE id=?", (json.dumps(capsa), time.time(), pa))
    # Eigentuemer ohne formula1: Editor ueber das Heim -> 404 mit Hinweis
    capsb = json.loads(sql("b", "SELECT caps FROM fed_peer")[0][0])
    sql("b", "UPDATE fed_peer SET caps=?, caps_at=? WHERE id=?", (json.dumps([x for x in capsb if x != "formula1"]), time.time(), pb))
    s, d = api("b", "fb-bert", "POST", f"/api/notes/{rDB}/formula/check", {"expr": "1"})
    ok(f"Eigentuemer ohne formula1: Editor -> 404 mit Hinweis ({s})", s == 404 and "ältere" in str(d), d)
    sql("b", "UPDATE fed_peer SET caps=?, caps_at=? WHERE id=?", (json.dumps(capsb), time.time(), pb))
    s, d = api("b", "fb-olga", "GET", f"/api/notes/{rDB}/rows")
    ok(f"B: Olga ohne Freigabe -> keine Zeilen ({s})", s in (403, 404) and "rows" not in (d or {}), d)


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
    print(f"\nFOEDERATION-FORMELN {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    run()
