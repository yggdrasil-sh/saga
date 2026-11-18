"""2.5 (Bugs-DB 2156): Saga-Siegel als Seitensymbol ueber Servergrenzen - zwei eigene Saga-Instanzen A und B (Geruest wie
tests/foederation_widgets.py).

Anna (A) hat eine Seite mit Siegel und eine Unterseite mit Siegel; sie teilt die Seite mit Bert auf B (bearbeiten). Geprueft:
- Faehigkeit siegel1 im hello (remote.caps).
- B liest die Seite: Symbol = derselbe Pfad /siegel/<farbe>/<motiv>.svg (gibt es auf B genauso, kein Proxy), auch in der
  Liste geteilter Seiten und bei der Unterseite.
- B setzt ein anderes Siegel -> bei A gespeichert; unbekanntes Siegel -> abgelehnt, nichts geaendert.
- B legt eine Unterseite mit Siegel an -> bei A mit Siegel.
- A ohne siegel1 (aelterer Server, auf B so eingetragen): B setzt kein Siegel (409, A unveraendert); Emoji geht weiter.
Ports: FEDSG_PORTS="a,a-admin,b,b-admin" (Standard 8875,8876,8877,8878). Raeumt auf (Datenbanken geloescht)."""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foederation as T  # noqa: E402
import foederation2 as F2  # noqa: E402

pa_, aa_, pb_, ab_ = (int(x) for x in os.environ.get("FEDSG_PORTS", "8875,8876,8877,8878").split(","))
T.I["a"].update(port=pa_, admin=aa_, db="saga_fsga", url=f"http://127.0.0.1:{pa_}", host=f"127.0.0.1:{pa_}", state="/tmp/fedsg-a",
                allow=f"localhost:{pb_}")
T.I["b"].update(port=pb_, admin=ab_, db="saga_fsgb", url=f"http://localhost:{pb_}", host=f"localhost:{pb_}", state="/tmp/fedsg-b",
                allow=f"127.0.0.1:{pa_}")
HA, HB = T.I["a"]["host"], T.I["b"]["host"]
api, sql = T.api, T.sql
R, R2, R3 = "/siegel/weinrot/rabe.svg", "/siegel/smaragd/kompass.svg", "/siegel/elfenbein/kerze.svg"


def ok(n, c, info=None):
    T.ok(n if c or info is None else f"{n} -> {str(info)[:300]}", c)


def ref(pid, rid):
    return -(rid * 1024 + pid)


def part(pa, pb):
    an, bb = "fa-anna", "fb-bert"
    s, d = api("a", an, "POST", "/api/notes", {"title": "Siegel geteilt", "icon": R})
    seite = d["id"]
    kind = api("a", an, "POST", "/api/notes", {"title": "Kind", "parent_id": seite, "icon": R3})[1]["id"]
    ok(f"A: Seite und Unterseite mit Siegel ({s})", s == 200)
    s, _ = api("a", an, "PUT", f"/api/notes/{seite}/shares", {"type": "remote", "target": f"@bert@{HB}", "role": "edit"})
    ok(f"A: mit Bert@B geteilt (bearbeiten) ({s})", s == 200)
    ok("B: Freigabe angekommen", T.wait(lambda: len(sql("b", "SELECT 1 FROM fed_inbound")) >= 1, 20))
    rS = ref(pa, seite)
    s, d = api("b", bb, "GET", f"/api/notes/{rS}")
    ok(f"B: Seite lesen, siegel1 gemeldet ({s})", s == 200 and "siegel1" in (d.get("remote") or {}).get("caps", []), d.get("remote"))
    ok("B: Symbol = Siegel-Pfad (unveraendert)", d.get("icon") == R, d.get("icon"))
    s, lst = api("b", bb, "GET", "/api/fed/shared")
    lst = lst if isinstance(lst, list) else (lst or {}).get("notes", (lst or {}).get("items", []))
    ok("B: Liste zeigt das Siegel der geteilten Seite", any(x.get("id") == rS and x.get("icon") == R for x in lst), [x.get("icon") for x in lst][:8])
    s, d = api("b", bb, "GET", f"/api/notes/{ref(pa, kind)}")
    ok(f"B: Unterseite mit Siegel ({s})", s == 200 and d.get("icon") == R3, d.get("icon") if isinstance(d, dict) else d)
    s = T.hreq("b", "GET", "/siegel/weinrot/rabe.svg")[0]
    ok(f"B: Datei liegt auf B selbst ({s})", s == 200)

    s, d = api("b", bb, "PATCH", f"/api/notes/{rS}", {"icon": R2})
    ok(f"B: anderes Siegel setzen ({s})", s == 200, d)
    ok("A: Siegel gespeichert", T.wait(lambda: sql("a", "SELECT icon FROM note WHERE id=?", (seite,))[0][0] == R2, 10),
       sql("a", "SELECT icon FROM note WHERE id=?", (seite,)))
    s, d = api("b", bb, "PATCH", f"/api/notes/{rS}", {"icon": "/siegel/smaragd/nix.svg"})
    ok(f"B: unbekanntes Siegel -> 400, A unveraendert ({s})", s == 400 and sql("a", "SELECT icon FROM note WHERE id=?", (seite,))[0][0] == R2, d)
    s, d = api("b", bb, "POST", f"/api/notes", {"title": "Von Bert", "parent_id": rS, "icon": R3})
    nid = d.get("id") if isinstance(d, dict) else None
    row = sql("a", "SELECT icon FROM note WHERE parent_id=? AND title='Von Bert'", (seite,))
    ok(f"B: Unterseite mit Siegel bei A angelegt ({s})", s == 200 and nid and row and row[0][0] == R3, (d, row))

    # A ohne siegel1 (aelterer Server): B schickt kein Siegel
    caps = json.loads(sql("b", "SELECT caps FROM fed_peer")[0][0])
    sql("b", "UPDATE fed_peer SET caps=?, caps_at=? WHERE id=?", (json.dumps([x for x in caps if x != "siegel1"]), time.time(), pb))
    s, d = api("b", bb, "GET", f"/api/notes/{rS}")
    ok("B: ohne siegel1 fehlt die Faehigkeit in remote.caps (Web blendet die Auswahl aus)", "siegel1" not in (d.get("remote") or {}).get("caps", []))
    s, d = api("b", bb, "PATCH", f"/api/notes/{rS}", {"icon": R})
    ok(f"B: A ohne siegel1 -> 409, A unveraendert ({s})", s == 409 and "Siegel" in str(d.get("error", "")) and
       sql("a", "SELECT icon FROM note WHERE id=?", (seite,))[0][0] == R2, d)
    s, d = api("b", bb, "PATCH", f"/api/notes/{rS}", {"icon": "🦉"})
    ok(f"B: Emoji geht weiter ({s})", s == 200 and T.wait(lambda: sql("a", "SELECT icon FROM note WHERE id=?", (seite,))[0][0] == "🦉", 10))
    sql("b", "UPDATE fed_peer SET caps=?, caps_at=? WHERE id=?", (json.dumps(caps), time.time(), pb))


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
    print(f"\nFOEDERATION-SIEGEL {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    run()
