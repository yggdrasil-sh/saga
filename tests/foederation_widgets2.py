"""2.5 Paket 4: Widgets Etappe 2 + Diagramme ueber Servergrenzen (Faehigkeit widgets2) - zwei eigene Saga-Instanzen A und B
(Geruest wie tests/foederation_widgets.py).

Anna (A) hat eine Seite mit Wetter, Gewohnheiten, Anstehendes und Diagramm; sie teilt Seite und Datenbank mit Bert (B).
Geprueft:
- widgets2 in remote.caps; B fragt GET /api/notes/{-id}/widgets -> A rechnet: Wetter (Fake-Open-Meteo auf A), Gewohnheiten
  (can_check nach Bert's Rolle an der Datenbank), Diagramm; Anstehendes IMMER no_access (persoenlich, kein Leck von Annas
  Aufgaben); W.data wird auf B gefiltert (bekannte Felder).
- Abhaken ueber Servergrenzen (POST /api/notes/{-id}/widgets/{block}/habit) mit Bert's Rechten (Lesen -> 403, Bearbeiten -> ok).
- Diagramm-Ansicht einer fremden Datenbank (GET /api/notes/{-db}/chart).
- Gegenstelle ohne widgets2: neue Arten fehlen, unsupported2; Abhaken 409; Diagramm unsupported. Ohne widgets1: unsupported.
Ports: FEDWG2_PORTS="a,a-admin,b,b-admin,wetter" (Standard 8260,8261,8262,8263,8264). Raeumt auf (Datenbanken geloescht)."""
import json, os, sys, threading, time
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foederation as T  # noqa: E402
import foederation2 as F2  # noqa: E402

pa_, aa_, pb_, ab_, wx_ = (int(x) for x in os.environ.get("FEDWG2_PORTS", "8260,8261,8262,8263,8264").split(","))
T.I["a"].update(port=pa_, admin=aa_, db="saga_fwg2a", url=f"http://127.0.0.1:{pa_}", host=f"127.0.0.1:{pa_}", state="/tmp/fedwg2-a",
                allow=f"localhost:{pb_}, 127.0.0.1:{wx_}")
T.I["b"].update(port=pb_, admin=ab_, db="saga_fwg2b", url=f"http://localhost:{pb_}", host=f"localhost:{pb_}", state="/tmp/fedwg2-b",
                allow=f"127.0.0.1:{pa_}")
api, sql = T.api, T.sql
TODAY = date.today()


def ok(n, c, info=None):
    T.ok(n if c or info is None else f"{n} -> {str(info)[:400]}", c)


def ref(pid, rid):
    return -(rid * 1024 + pid)


def W(bid, kind, cfg, **kw):
    return {"id": bid, "type": "widget", "data": dict({"v": 1, "kind": kind, "cfg": cfg}, **kw)}


class WXH(BaseHTTPRequestHandler):
    hits = 0

    def log_message(self, *a):
        pass

    def do_GET(self):
        WXH.hits += 1
        days = [(TODAY + timedelta(days=i)).isoformat() for i in range(8)]
        b = json.dumps({"current": {"temperature_2m": 7.5, "weather_code": 61, "wind_speed_10m": 20},
                        "daily": {"time": days, "weather_code": [61] * 8, "temperature_2m_max": [9] * 8, "temperature_2m_min": [2] * 8,
                                  "precipitation_probability_max": [80] * 8}}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)


def set_caps(k, peer_id, drop):
    caps = json.loads(sql(k, "SELECT caps FROM fed_peer WHERE id=?", (peer_id,))[0][0])
    sql(k, "UPDATE fed_peer SET caps=?, caps_at=? WHERE id=?", (json.dumps([x for x in caps if x not in drop]), time.time(), peer_id))
    return caps


def part(pa, pb):
    an = "fa-anna"
    sql("a", "INSERT INTO setting (k, v) VALUES ('weather', ?) ON CONFLICT (k) DO UPDATE SET v=EXCLUDED.v",
        (json.dumps({"enabled": True, "base_url": f"http://127.0.0.1:{wx_}/v1/forecast", "geo_url": "", "api_key": ""}),))
    time.sleep(5.5)                      # Einstellungen sind im Server 5 s zwischengespeichert
    db = api("a", an, "POST", "/api/notes", {"title": "Annas Log", "kind": "database"})[1]["id"]
    api("a", an, "PATCH", f"/api/notes/{db}", {"schema": {"props": [
        {"id": "datum", "name": "Datum", "type": "date"}, {"id": "sport", "name": "Sport", "type": "checkbox"},
        {"id": "kat", "name": "Kat", "type": "select", "options": [{"id": "x", "name": "X", "color": "red"}, {"id": "y", "name": "Y"}]}],
        "views": [{"id": "alle", "name": "Alle", "type": "table"},
                  {"id": "dia", "name": "Dia", "type": "chart", "chart": {"type": "bar", "x": "kat"}}]}})
    for i, (sp, k) in enumerate(((True, "x"), (True, "y"), (False, "x"))):
        d = (TODAY - timedelta(days=i)).isoformat()
        api("a", an, "POST", "/api/notes", {"title": d, "parent_id": db, "props": {"datum": d, "sport": sp, "kat": k}})
    api("a", an, "POST", "/api/tasks", {"text": "Geheime Aufgabe Anna", "due": TODAY.isoformat()})
    seite = api("a", an, "POST", "/api/notes", {"title": "Cockpit2"})[1]["id"]
    s, _ = api("a", an, "PATCH", f"/api/notes/{seite}", {"format": "blocks", "body": json.dumps({"blocks": [
        W("wx", "weather", {"place": "Kiel", "lat": 54.32, "lon": 10.12, "days": 3}),
        W("hb", "habit", {"db": db, "view": "alle", "date": "datum", "habits": ["sport"], "range": 7}),
        W("up", "upcoming", {"days": 7}),
        W("ch", "chart", {"db": db, "type": "pie", "x": "kat"}),
        W("met", "metric", {"db": db, "agg": "count"})]})})
    ok(f"A: Seite mit Etappe-2-Widgets ({s})", s == 200)
    s, d = api("a", an, "GET", f"/api/notes/{seite}/widgets")
    ok(f"A: lokal gerechnet (Wetter vom Fake, Anstehendes mit eigener Aufgabe) ({s})", s == 200 and d["widgets"]["wx"]["data"]["now"]["t"] == 7.5
       and any(x["title"] == "Geheime Aufgabe Anna" for x in d["widgets"]["up"]["data"]["items"]) if s == 200 and d["widgets"]["wx"]["data"] else False, d)
    for nid in (seite, db):
        api("a", an, "PUT", f"/api/notes/{nid}/shares", {"type": "remote", "target": f"@bert@{T.I['b']['host']}", "role": "view"})
    ok("B: Freigaben angekommen", T.wait(lambda: len(sql("b", "SELECT 1 FROM fed_inbound")) >= 2, 20))
    rS, rD = ref(pa, seite), ref(pa, db)
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rS}")
    ok(f"B: widgets2 in remote.caps ({s})", s == 200 and "widgets2" in (d.get("remote") or {}).get("caps", []), d.get("remote"))

    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rS}/widgets?tz=Europe/Berlin")
    w = d.get("widgets", {}) if isinstance(d, dict) else {}
    raw = json.dumps(d, ensure_ascii=False)
    ok(f"B: Wetter von A gerechnet ({s})", w.get("wx", {}).get("data", {}).get("now", {}).get("t") == 7.5
       and len(w["wx"]["data"]["days"]) == 4 and w["wx"]["data"]["place"] == "Kiel", w.get("wx"))
    hb = w.get("hb", {}).get("data") or {}
    ok("B: Gewohnheiten (DB lesen -> can_check false)", hb.get("habits", [{}])[0].get("done", [])[-2:] == [True, True] and hb.get("can_check") is False, w.get("hb"))
    ok("B: Anstehendes ueber Servergrenzen -> no_access, kein Leck", w.get("up", {}).get("error") == "no_access" and w["up"]["data"] is None
       and "Geheime Aufgabe" not in raw, w.get("up"))
    ok("B: Diagramm von A", [c["key"] for c in w.get("ch", {}).get("data", {}).get("cats", [])] == ["x", "y"]
       and w["ch"]["data"]["series"][0]["values"] == [2, 1], w.get("ch"))
    ok("B: W-Format mit data", all(set(x) == {"kind", "value", "max", "text", "fmt", "as_of", "error", "data"} for k, x in w.items() if k != "met")
       and set(w.get("met", {})) == {"kind", "value", "max", "text", "fmt", "as_of", "error"}, w)
    ok("B: kein unsupported2", "unsupported2" not in d)

    day = TODAY.isoformat()
    s, d = api("b", "fb-bert", "POST", f"/api/notes/{rS}/widgets/hb/habit", {"pid": "sport", "day": day, "on": False})
    ok(f"B: Abhaken mit Leserecht an der DB -> abgelehnt ({s})", s in (403, 404), d)
    api("a", an, "PUT", f"/api/notes/{db}/shares", {"type": "remote", "target": f"@bert@{T.I['b']['host']}", "role": "edit"})
    time.sleep(0.3)
    s, d = api("b", "fb-bert", "POST", f"/api/notes/{rS}/widgets/hb/habit?tz=Europe/Berlin", {"pid": "sport", "day": day, "on": False})
    ok(f"B: Abhaken mit Bearbeiten -> neuer Stand ({s})", s == 200 and d["widget"]["data"]["habits"][0]["done"][-1] is False
       and d["widget"]["data"]["can_check"] is True, d)
    s, rows = api("a", an, "GET", f"/api/notes/{db}/rows")
    ok("A: Haekchen in Annas Datenbank entfernt", s == 200 and not any(r["props"].get("sport") for r in rows["rows"] if r["props"].get("datum") == day))

    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rD}/chart?view=dia")
    ok(f"B: Diagramm-Ansicht der fremden Datenbank ({s})", s == 200 and d.get("error") is None and d["chart"]["series"][0]["values"] == [2, 1], d)

    s, d = api("b", "fb-olga", "GET", f"/api/notes/{rS}/widgets")
    ok(f"B: Olga ohne Freigabe -> keine Werte ({s})", s in (403, 404) and "widgets" not in (d or {}))

    # Gegenstelle ohne widgets2 (aelterer Server): Etappe-1-Werte ja, neue Arten nicht
    old = set_caps("b", pb, {"widgets2"})
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rS}/widgets")
    ok(f"B: A ohne widgets2 -> nur Etappe 1, unsupported2 ({s})", s == 200 and d.get("unsupported2") is True and "met" in d["widgets"]
       and not any(k in d["widgets"] for k in ("wx", "hb", "up", "ch")), d)
    s, d = api("b", "fb-bert", "POST", f"/api/notes/{rS}/widgets/hb/habit", {"pid": "sport", "day": day, "on": True})
    ok(f"B: Abhaken ohne widgets2 -> 409 ({s})", s == 409)
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rD}/chart?view=dia")
    ok(f"B: Diagramm ohne widgets2 -> unsupported ({s})", s == 200 and d.get("error") == "unsupported")
    sql("b", "UPDATE fed_peer SET caps=?, caps_at=? WHERE id=?", (json.dumps(old), time.time(), pb))
    # A selbst laesst die Etappe-2-Arten weg, wenn B widgets2 nicht meldet
    olda = set_caps("a", pa, {"widgets2"})
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rS}/widgets")
    ok(f"A: Gegenstelle ohne widgets2 -> neue Arten fehlen ({s})", s == 200 and not any(k in d.get("widgets", {}) for k in ("wx", "hb", "up", "ch"))
       and "met" in d.get("widgets", {}), d)
    s, d = api("b", "fb-bert", "POST", f"/api/notes/{rS}/widgets/hb/habit", {"pid": "sport", "day": day, "on": True})
    ok(f"A: Abhaken von Gegenstelle ohne widgets2 abgelehnt ({s})", s != 200)
    sql("a", "UPDATE fed_peer SET caps=?, caps_at=? WHERE id=?", (json.dumps(olda), time.time(), pa))
    set_caps("b", pb, {"widgets1"})
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rS}/widgets")
    ok(f"B: A ohne widgets1 -> unsupported ({s})", s == 200 and d == {"widgets": {}, "unsupported": True}, d)
    sql("b", "UPDATE fed_peer SET caps=?, caps_at=? WHERE id=?", (json.dumps(old), time.time(), pb))


def run():
    srv = ThreadingHTTPServer(("127.0.0.1", wx_), WXH)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
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
    print(f"\nFOEDERATION-WIDGETS2 {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    run()
