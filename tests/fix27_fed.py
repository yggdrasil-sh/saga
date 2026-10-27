"""2.5 Fix-Runde 27.10., Foederation (aus tests/fix27_srv.py, auch allein lauffaehig): zwei eigene Instanzen A und B
(Geruest tests/foederation2.py). Anna (A) teilt eine Datenbank mit Eintrags-Vorlagen mit Bert@B (Bearbeiten) und
Olga@B (Lesen).
- E1: B bekommt templates (negative IDs) und default_tpl als negative ID; Bert legt ohne via aus der Vorlage an
  (Titel leer, fresh/placeholder), Olga bekommt keine templates und legt nichts an; fremde Vorlage/anderes Ziel nicht.
- E4: Bert verwirft den unveraenderten Eintrag ueber B (auf A endgueltig weg), geaendert bleibt er; Anna kann Berts
  Eintrag nicht verwerfen.
- Aeltere Gegenstelle (A ohne rowtpl): templates leer, Anlegen ohne via mit Hinweis, Schaltflaechen (tplbtn) gehen weiter.
Ports: FIX27_FED_PORTS="a,a-admin,b,b-admin" (Standard 8594,8595,8596,8597). Raeumt auf (Datenbanken geloescht)."""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foederation as T  # noqa: E402
import foederation2 as F2  # noqa: E402

pa_, aa_, pb_, ab_ = (int(x) for x in os.environ.get("FIX27_FED_PORTS", "8594,8595,8596,8597").split(","))
T.I["a"].update(port=pa_, admin=aa_, db="saga_fx27a", url=f"http://127.0.0.1:{pa_}", host=f"127.0.0.1:{pa_}", state="/tmp/fx27fed-a",
                allow=f"localhost:{pb_}")
T.I["b"].update(port=pb_, admin=ab_, db="saga_fx27b", url=f"http://localhost:{pb_}", host=f"localhost:{pb_}", state="/tmp/fx27fed-b",
                allow=f"127.0.0.1:{pa_}")
HA, HB = T.I["a"]["host"], T.I["b"]["host"]
ok, api, sql = T.ok, T.api, T.sql
SCHEMA = {"props": [{"id": "prio", "name": "Prio", "type": "select", "options": [{"id": "hoch", "name": "Hoch", "color": "red"}]}],
          "views": [{"id": "tabelle", "name": "Tabelle", "type": "table"}]}


def ref(pid, rid):
    return -(rid * 1024 + pid)


def unref(pa, x):
    return (-x - pa) // 1024


def part(pa, pb):
    an = "fa-anna"
    db = api("a", an, "POST", "/api/notes", {"title": "Aufgaben", "kind": "database"})[1]["id"]
    api("a", an, "PATCH", f"/api/notes/{db}", {"schema": SCHEMA})
    tpl = api("a", an, "POST", "/api/notes", {"title": "Neue Aufgabe", "parent_id": db, "icon": "☑️", "props": {"prio": "hoch"}})[1]["id"]
    api("a", an, "PATCH", f"/api/notes/{tpl}", {"template": True, "format": "blocks",
                                                "body": json.dumps({"blocks": [{"type": "paragraph", "data": {"text": "Was ist zu tun?"}}]})})
    s, _ = api("a", an, "PATCH", f"/api/notes/{db}", {"schema": dict(SCHEMA, default_tpl=tpl)})
    ok(f"A: Datenbank mit Standard-Vorlage ({s})", s == 200)
    andere = api("a", an, "POST", "/api/notes", {"title": "Privat", "kind": "database"})[1]["id"]
    geheim = api("a", an, "POST", "/api/notes", {"title": "Geheim", "parent_id": andere})[1]["id"]
    api("a", an, "PATCH", f"/api/notes/{geheim}", {"template": True})
    for who, role in (("bert", "edit"), ("olga", "view")):
        s, _ = api("a", an, "PUT", f"/api/notes/{db}/shares", {"type": "remote", "target": f"@{who}@{HB}", "role": role})
        ok(f"A: Datenbank mit {who}@B geteilt ({role}, {s})", s == 200)
    ok("B: Freigaben angekommen", T.wait(lambda: len(sql("b", "SELECT 1 FROM fed_inbound")) >= 2, 20))
    rD, rT = ref(pa, db), ref(pa, tpl)

    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rD}/rows")
    ok(f"E1 fed: Bert bekommt templates als negative IDs und default_tpl als negative ID ({s}, {str(d.get('templates'))[:80]})",
       s == 200 and d.get("templates") == [{"id": rT, "title": "Neue Aufgabe", "icon": "☑️"}] and d["schema"].get("default_tpl") == rT
       and d.get("role") == "edit")
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rD}")
    ok(f"E1 fed: Faehigkeit rowtpl gemeldet ({s})", "rowtpl" in (d.get("remote") or {}).get("caps", []))
    s, d = api("b", "fb-olga", "GET", f"/api/notes/{rD}/rows")
    ok(f"E1 fed: Olga (Lesen) ohne templates ({s})", s == 200 and d.get("templates") == [])

    def count():
        return sql("a", "SELECT count(*) FROM note WHERE parent_id=? AND is_template=0", (db,))[0][0]
    s, d = api("b", "fb-bert", "POST", f"/api/notes/{rT}/duplicate", {"parent_id": rD})
    nid = d.get("id") if isinstance(d, dict) else None
    ok(f"E1 fed: Bert legt ohne via aus der Vorlage an, fresh/placeholder ({s}, {str(d)[:100]})",
       s == 200 and isinstance(nid, int) and nid < 0 and isinstance(d.get("fresh"), str) and d.get("placeholder") == "Neue Aufgabe")
    r = sql("a", "SELECT uid, parent_id, title, icon, props, body, created_by FROM note WHERE id=?", (unref(pa, nid),)) if nid else []
    ok("E1 fed: auf A in der Datenbank, Titel leer, Werte/Inhalt aus der Vorlage, angelegt von Bert",
       bool(r) and r[0][0] == an and r[0][1] == db and r[0][2] == "" and json.loads(r[0][4] or "{}").get("prio") == "hoch"
       and "Was ist zu tun" in r[0][5] and "bert" in str(r[0][6]))
    n0 = count()
    for name, (tp, b, who) in {"Olga (Lesen)": (rT, {"parent_id": rD}, "fb-olga"),
                               "fremde Vorlage": (ref(pa, geheim), {"parent_id": rD}, "fb-bert"),
                               "anderes Ziel": (rT, {"parent_id": ref(pa, andere)}, "fb-bert"),
                               "Ziel als ID von B": (rT, {"parent_id": db}, "fb-bert")}.items():
        s, _ = api("b", who, "POST", f"/api/notes/{tp}/duplicate", b)
        ok(f"E1 fed: {name} -> nichts angelegt ({s})", s in (403, 404) and count() == n0)

    # E4 ueber die Foederation
    if nid:
        s, _ = api("a", an, "POST", f"/api/notes/{unref(pa, nid)}/discard", {"fresh": d["fresh"]})
        ok(f"E4 fed: Anna kann Berts neuen Eintrag nicht verwerfen ({s})", s == 404)
        s, dd = api("b", "fb-bert", "POST", f"/api/notes/{nid}/discard", {"fresh": d["fresh"]})
        ok(f"E4 fed: Bert verwirft unveraendert -> auf A endgueltig weg ({s}, {dd})",
           s == 200 and dd.get("deleted") is True and not sql("a", "SELECT 1 FROM note WHERE id=?", (unref(pa, nid),)))
    s, d = api("b", "fb-bert", "POST", f"/api/notes/{rT}/duplicate", {"parent_id": rD})
    nid = d.get("id")
    api("b", "fb-bert", "PATCH", f"/api/notes/{nid}", {"title": "Rasen maehen"})
    s, dd = api("b", "fb-bert", "POST", f"/api/notes/{nid}/discard", {"fresh": d["fresh"]})
    ok(f"E4 fed: geaendert -> bleibt (409, {s}, {dd})", s == 409 and dd.get("reason") == "changed" and dd.get("deleted") is False
       and sql("a", "SELECT title FROM note WHERE id=?", (unref(pa, nid),))[0][0] == "Rasen maehen")

    # Aeltere Gegenstelle: A meldet kein rowtpl (B sieht A ohne Faehigkeit) bzw. liefert kein Feld
    caps = json.loads(sql("b", "SELECT caps FROM fed_peer")[0][0])
    sql("b", "UPDATE fed_peer SET caps=?, caps_at=? WHERE id=?", (json.dumps([x for x in caps if x != "rowtpl"]), time.time(), pb))
    n0 = count()
    s, d = api("b", "fb-bert", "POST", f"/api/notes/{rT}/duplicate", {"parent_id": rD})
    ok(f"E1 fed: A ohne rowtpl -> Hinweis 'kann das noch nicht', nichts angelegt ({s}, {str(d)[:70]})",
       s == 404 and HA in str(d.get("error", "")) and count() == n0)
    s, d = api("b", "fb-bert", "POST", f"/api/notes/{ref(pa, 99999)}/discard", {"fresh": "x" * 20})
    ok(f"E4 fed: A ohne rowtpl -> discard mit Hinweis ({s})", s == 404 and HA in str(d.get("error", "")))
    sql("b", "UPDATE fed_peer SET caps=?, caps_at=? WHERE id=?", (json.dumps(caps), time.time(), pb))
    # Antwort einer aelteren Gegenstelle ohne templates-Feld: tests/fix27_srv.py (fed_unit) prueft die Umsetzung direkt


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
    return list(T.res)


if __name__ == "__main__":
    r = run()
    print(f"\nFIX27-FOEDERATION {sum(r)}/{len(r)} OK – " + ("ALLES GRUEN" if all(r) else "FEHLER"))
    sys.exit(0 if all(r) else 1)
