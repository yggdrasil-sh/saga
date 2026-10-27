"""2.5: Widgets in Seiten ueber Servergrenzen - zwei eigene Saga-Instanzen A und B (Geruest wie tests/foederation2.py).

Anna (A) hat eine Seite "Cockpit" mit Widgets (Kennzahl/Fortschritt aus einer Datenbank, festes Zitat); sie teilt die
Seite mit Bert auf B. Geprueft:
- Faehigkeit widgets1 im hello (remote.caps).
- B fragt GET /api/notes/{-id}/widgets -> A rechnet mit dem Prinzipal @bert@B: Datenbank nicht geteilt -> no_access ohne
  Werte/Namen; Datenbank mit Bert geteilt -> Werte; feste Zitate immer.
- Antwort von A wird auf B gefiltert (nur bekannte Felder).
- Gegenstelle ohne widgets1 -> {"widgets": {}, "unsupported": true}; Seite ohne Freigabe -> Fehler, keine Werte.
Ports: FEDWG_PORTS="a,a-admin,b,b-admin" (Standard 8476,8477,8478,8479). Raeumt auf (Datenbanken geloescht)."""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foederation as T  # noqa: E402
import foederation2 as F2  # noqa: E402

pa_, aa_, pb_, ab_ = (int(x) for x in os.environ.get("FEDWG_PORTS", "8476,8477,8478,8479").split(","))
T.I["a"].update(port=pa_, admin=aa_, db="saga_fwga", url=f"http://127.0.0.1:{pa_}", host=f"127.0.0.1:{pa_}", state="/tmp/fedwg-a",
                allow=f"localhost:{pb_}")
T.I["b"].update(port=pb_, admin=ab_, db="saga_fwgb", url=f"http://localhost:{pb_}", host=f"localhost:{pb_}", state="/tmp/fedwg-b",
                allow=f"127.0.0.1:{pa_}")
HA, HB = T.I["a"]["host"], T.I["b"]["host"]
api, sql = T.api, T.sql


def ok(n, c, info=None):
    T.ok(n if c or info is None else f"{n} -> {str(info)[:300]}", c)


def ref(pid, rid):
    return -(rid * 1024 + pid)


def W(bid, kind, cfg, **kw):
    return {"id": bid, "type": "widget", "data": dict({"v": 1, "kind": kind, "cfg": cfg}, **kw)}


def part(pa, pb):
    an = "fa-anna"
    db = api("a", an, "POST", "/api/notes", {"title": "Geheime Zahlen", "kind": "database"})[1]["id"]
    api("a", an, "PATCH", f"/api/notes/{db}", {"schema": {"props": [{"id": "n", "name": "N", "type": "number"},
                                                                   {"id": "ok", "name": "OK", "type": "checkbox"}]}})
    for i, (n, c) in enumerate(((4, True), (6, False), (10, True))):
        api("a", an, "POST", "/api/notes", {"title": f"z{i}", "parent_id": db, "props": {"n": n, "ok": c}})
    seite = api("a", an, "POST", "/api/notes", {"title": "Cockpit"})[1]["id"]
    s, _ = api("a", an, "PATCH", f"/api/notes/{seite}", {"format": "blocks", "body": json.dumps({"blocks": [
        W("sum", "metric", {"db": db, "agg": "sum", "prop": "n"}, title="Summe"),
        W("prog", "progress", {"src": "db", "db": db, "mode": "checked", "prop": "ok"}),
        W("zit", "quote", {"src": "fixed", "items": ["Nur ein Zitat"]}),
        W("clk", "clock", {"tz": "UTC"})]})})
    ok(f"A: Seite mit Widgets ({s})", s == 200)
    s, _ = api("a", an, "PUT", f"/api/notes/{seite}/shares", {"type": "remote", "target": f"@bert@{HB}", "role": "view"})
    ok(f"A: Cockpit mit Bert@B geteilt ({s})", s == 200)
    ok("B: Freigabe angekommen", T.wait(lambda: len(sql("b", "SELECT 1 FROM fed_inbound")) >= 1, 20))
    rS = ref(pa, seite)
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rS}")
    ok(f"B: Seite lesen, Faehigkeit widgets1 gemeldet ({s})", s == 200 and "widgets1" in (d.get("remote") or {}).get("caps", []), d.get("remote"))
    body = json.loads(d.get("body") or "{}") if s == 200 else {}
    ok("B: Widget-Bloecke unveraendert im Inhalt (ids, Arten)", [b.get("id") for b in body.get("blocks", [])] == ["sum", "prog", "zit", "clk"])

    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rS}/widgets?tz=Europe/Berlin")
    w = d.get("widgets", {}) if isinstance(d, dict) else {}
    raw = json.dumps(d, ensure_ascii=False)
    ok(f"B: Widgets ueber A gerechnet, DB nicht geteilt -> no_access ({s})", s == 200 and w.get("sum", {}).get("error") == "no_access"
       and w.get("prog", {}).get("error") == "no_access" and w["sum"]["value"] is None, d)
    ok("B: kein Name der Datenbank in der Antwort", "Geheime" not in raw)
    ok("B: festes Zitat kommt an", w.get("zit", {}).get("text") == "Nur ein Zitat", w.get("zit"))
    ok("B: Uhr wird nicht gerechnet (nur Client)", "clk" not in w)

    s, _ = api("a", an, "PUT", f"/api/notes/{db}/shares", {"type": "remote", "target": f"@bert@{HB}", "role": "view"})
    time.sleep(0.3)
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rS}/widgets")
    w = d.get("widgets", {}) if isinstance(d, dict) else {}
    ok(f"B: DB mit Bert geteilt -> Werte (Summe 20, 2 von 3) ({s})", s == 200 and w.get("sum", {}).get("value") == 20
       and w.get("prog", {}).get("value") == 2 and w["prog"].get("max") == 3, w)
    ok("B: Antwortformat W", all(set(x) == {"kind", "value", "max", "text", "fmt", "as_of", "error"} for x in w.values()), w)

    s, d = api("b", "fb-olga", "GET", f"/api/notes/{rS}/widgets")
    ok(f"B: Olga ohne Freigabe -> keine Werte ({s})", s in (403, 404) and "widgets" not in (d or {}), d)

    # Gegenstelle ohne widgets1 (aelterer Server): Hinweis statt Fehler
    caps = json.loads(sql("b", "SELECT caps FROM fed_peer")[0][0])
    sql("b", "UPDATE fed_peer SET caps=?, caps_at=? WHERE id=?", (json.dumps([x for x in caps if x != "widgets1"]), time.time(), pb))
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rS}/widgets")
    ok(f"B: A ohne widgets1 -> unsupported ({s}, {d})", s == 200 and d == {"widgets": {}, "unsupported": True})
    sql("b", "UPDATE fed_peer SET caps=?, caps_at=? WHERE id=?", (json.dumps(caps), time.time(), pb))
    # A selbst lehnt ab, wenn B widgets1 nicht meldet
    capsa = json.loads(sql("a", "SELECT caps FROM fed_peer")[0][0])
    sql("a", "UPDATE fed_peer SET caps=?, caps_at=? WHERE id=?", (json.dumps([x for x in capsa if x != "widgets1"]), time.time(), pa))
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rS}/widgets")
    ok(f"A: Gegenstelle ohne widgets1 -> keine Werte ({s})", s != 200 or not (d.get("widgets") or {}).get("sum", {}).get("value"), d)
    sql("a", "UPDATE fed_peer SET caps=?, caps_at=? WHERE id=?", (json.dumps(capsa), time.time(), pa))


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
    print(f"\nFOEDERATION-WIDGETS {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    run()
