"""2.5 (tplbtn): Schaltflaeche "Neuer Eintrag" mit Vorlage auf einer Seite, die ueber Servergrenzen geteilt ist - zwei
eigene Saga-Instanzen A und B (eigene Datenbanken und Ports, Geruest wie tests/foederation2.py).

Anna (A) hat eine Seite "Bug Tracker" mit einer Datenbank darin und Schaltflaechen mit Vorlage (eine Vorlage in der
geteilten Datenbank, eine in einer privaten Vorlagen-Datenbank); sie teilt die Seite mit Bert auf B (Bearbeiten).
Geprueft:
- B bekommt Ziel und Vorlage der Schaltflaechen als negative IDs (Signatur bleibt), Speichern durch B schreibt sie
  zurueck in die IDs auf A (Signatur gilt weiter).
- Bert legt ueber B per Schaltflaeche einen Eintrag an: liegt auf A in der richtigen Datenbank, gehoert Anna, hat Inhalt,
  Symbol und Werte der Vorlage, ist keine Vorlage, "angelegt von" = Bert.
- Ohne via, ueber eine Seite ohne diese Schaltflaeche, mit anderer Vorlage, anderem Ziel, IDs eines anderen Servers,
  ohne Bearbeiten-Recht: nichts angelegt. Eine von B eingebaute neue Vorlage wirkt nicht.
- Schaltflaeche ohne Vorlage legt ueber B einen leeren Eintrag an.
Ports: FEDBTN_PORTS="a,a-admin,b,b-admin" (Standard 8490,8491,8492,8493). Raeumt auf (Datenbanken geloescht)."""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foederation as T  # noqa: E402
import foederation2 as F2  # noqa: E402

pa_, aa_, pb_, ab_ = (int(x) for x in os.environ.get("FEDBTN_PORTS", "8490,8491,8492,8493").split(","))
T.I["a"].update(port=pa_, admin=aa_, db="saga_fvta", url=f"http://127.0.0.1:{pa_}", host=f"127.0.0.1:{pa_}", state="/tmp/fedvorl-a",
                allow=f"localhost:{pb_}")
T.I["b"].update(port=pb_, admin=ab_, db="saga_fvtb", url=f"http://localhost:{pb_}", host=f"localhost:{pb_}", state="/tmp/fedvorl-b",
                allow=f"127.0.0.1:{pa_}")
HA, HB = T.I["a"]["host"], T.I["b"]["host"]
ok, api, sql = T.ok, T.api, T.sql
SCHEMA = {"props": [{"id": "prio", "name": "Prio", "type": "select", "options": [{"id": "hoch", "name": "Hoch", "color": "red"},
                                                                               {"id": "tief", "name": "Tief", "color": "gray"}]}],
          "views": [{"id": "tabelle", "name": "Tabelle", "type": "table"}]}


def ref(pid, rid):
    return -(rid * 1024 + pid)


def body_of(*bl):
    return json.dumps({"blocks": list(bl)})


def buttons_of(body):
    out = []

    def walk(v):
        if isinstance(v, dict):
            if v.get("type") == "buttons":
                out.extend((v.get("data") or {}).get("buttons") or [])
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(json.loads(body or "{}"))
    return out


def a_row(rid):
    r = sql("a", "SELECT uid, parent_id, body, icon, props, is_template, created_by FROM note WHERE id=?", (rid,))
    return r[0] if r else None


def part(pa, pb):
    an = "fa-anna"
    seite = api("a", an, "POST", "/api/notes", {"title": "Bug Tracker"})[1]["id"]
    db = api("a", an, "POST", "/api/notes", {"title": "Bugs", "kind": "database", "parent_id": seite})[1]["id"]
    s, _ = api("a", an, "PATCH", f"/api/notes/{db}", {"schema": SCHEMA})
    ok(f"A: Datenbank im Bug Tracker ({s})", s == 200)
    # Vorlage 1 in der geteilten Datenbank (wie meist), Vorlage 2 in einer privaten Vorlagen-Datenbank
    t1 = api("a", an, "POST", "/api/notes", {"title": "Feature Request", "parent_id": db, "icon": "💡", "props": {"prio": "tief"}})[1]["id"]
    api("a", an, "PATCH", f"/api/notes/{t1}", {"template": True, "format": "blocks",
                                               "body": body_of({"type": "paragraph", "data": {"text": "Was soll es koennen?"}})})
    tdb = api("a", an, "POST", "/api/notes", {"title": "Vorlagen (privat)", "kind": "database"})[1]["id"]
    api("a", an, "PATCH", f"/api/notes/{tdb}", {"schema": SCHEMA})
    t2 = api("a", an, "POST", "/api/notes", {"title": "Bug", "parent_id": tdb, "icon": "🔥", "props": {"prio": "hoch"}})[1]["id"]
    api("a", an, "PATCH", f"/api/notes/{t2}", {"template": True, "format": "blocks",
                                               "body": body_of({"type": "paragraph", "data": {"text": "Schritte zum Nachstellen"}})})
    geheim = api("a", an, "POST", "/api/notes", {"title": "Geheime Vorlage", "parent_id": tdb})[1]["id"]
    api("a", an, "PATCH", f"/api/notes/{geheim}", {"template": True})
    knoepfe = {"type": "buttons", "data": {"buttons": [
        {"label": "Feature Request", "icon": "💡", "action": "db", "target": db, "tpl": t1, "title": ""},
        {"label": "Bug Hinzufuegen", "icon": "🔥", "action": "db", "target": db, "tpl": t2, "title": "Bug {datum}"},
        {"label": "Leer", "action": "db", "target": db, "title": "Leerer Eintrag"},
        {"label": "Zur Datenbank", "action": "page", "target": db}]}}
    s, _ = api("a", an, "PATCH", f"/api/notes/{seite}", {"format": "blocks", "body": body_of(
        {"type": "columns", "data": {"cols": [{"blocks": [knoepfe]}]}}, {"type": "database", "data": {"db": db}})})
    ok(f"A: Seite mit Schaltflaechen gespeichert ({s})", s == 200)
    ohne = api("a", an, "POST", "/api/notes", {"title": "Ohne Knopf"})[1]["id"]
    for nid, role in ((seite, "edit"), (ohne, "edit")):
        s, _ = api("a", an, "PUT", f"/api/notes/{nid}/shares", {"type": "remote", "target": f"@bert@{HB}", "role": role})
        ok(f"A: Seite {nid} mit Bert@B geteilt ({s})", s == 200)
    s, _ = api("a", an, "PUT", f"/api/notes/{seite}/shares", {"type": "remote", "target": f"@olga@{HB}", "role": "view"})
    ok(f"A: Bug Tracker mit Olga@B nur zum Lesen ({s})", s == 200)
    ok("B: Freigaben angekommen", T.wait(lambda: len(T.sql("b", "SELECT 1 FROM fed_inbound")) >= 3, 20))

    rS, rD, rT1, rT2, rO = ref(pa, seite), ref(pa, db), ref(pa, t1), ref(pa, t2), ref(pa, ohne)
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rS}")
    bts = buttons_of(d.get("body")) if s == 200 else []
    ok(f"B: Seite lesen ({s}, {len(bts)} Schaltflaechen)", s == 200 and len(bts) == 4)
    ok("B: Ziel/Vorlage als negative IDs, Signatur bleibt",
       len(bts) == 4 and bts[0].get("target") == rD and bts[0].get("tpl") == rT1 and bts[1].get("tpl") == rT2 and bts[0].get("sig")
       and bts[1].get("sig") and bts[3].get("target") == rD)
    ok("B: Faehigkeit tplbtn gemeldet", "tplbtn" in (d.get("remote") or {}).get("caps", []))

    # Eintrag aus Vorlage 1 (in der geteilten Datenbank)
    s, d = api("b", "fb-bert", "POST", f"/api/notes/{rT1}/duplicate", {"parent_id": rD, "via": rS})
    nid = d.get("id") if isinstance(d, dict) else None
    ok(f"B: Feature Request per Schaltflaeche angelegt ({s}, {str(d)[:80]})", s == 200 and isinstance(nid, int) and nid < 0)
    r = a_row((-nid - pa) // 1024) if nid else None
    ok("A: Eintrag gehoert Anna, liegt in Bugs, Inhalt/Symbol/Werte der Vorlage, keine Vorlage, angelegt von Bert",
       bool(r) and r[0] == an and r[1] == db and "Was soll es koennen" in (r[2] or "") and r[3] == "💡"
       and json.loads(r[4] or "{}").get("prio") == "tief" and not r[5] and "bert" in str(r[6]))
    if nid:
        s, d = api("b", "fb-bert", "GET", f"/api/notes/{nid}")
        ok(f"B: neuer Eintrag oeffnen ({s})", s == 200 and d.get("parent_id") == rD and "Was soll es koennen" in d.get("body", ""))

    # Eintrag aus Vorlage 2 (private Vorlagen-Datenbank, nicht geteilt), mit Titel
    s, d = api("b", "fb-bert", "POST", f"/api/notes/{rT2}/duplicate", {"parent_id": rD, "via": rS, "title": "Bug 25.10."})
    nid2 = d.get("id") if isinstance(d, dict) else None
    r = a_row((-nid2 - pa) // 1024) if s == 200 and nid2 else None
    ok(f"B: Bug aus privater Vorlage angelegt ({s})", bool(r) and r[1] == db and "Nachstellen" in (r[2] or "") and r[3] == "🔥"
       and json.loads(r[4] or "{}").get("prio") == "hoch")
    ok("A: Titel uebernommen", bool(r) and sql("a", "SELECT title FROM note WHERE id=?", ((-nid2 - pa) // 1024,))[0][0] == "Bug 25.10.")
    ok("A: Vorlage selbst unveraendert in der privaten Datenbank", list(sql("a", "SELECT parent_id, is_template FROM note WHERE id=?", (t2,))[0]) == [tdb, 1])

    # Leere Schaltflaeche (ohne Vorlage)
    s, d = api("b", "fb-bert", "POST", "/api/notes", {"parent_id": rD, "title": "Leerer Eintrag"})
    ok(f"B: Schaltflaeche ohne Vorlage legt Eintrag an ({s})", s == 200 and sql("a", "SELECT parent_id FROM note WHERE title='Leerer Eintrag'")[0][0] == db)

    def count():
        return sql("a", "SELECT count(*) FROM note WHERE parent_id=? AND is_template=0", (db,))[0][0]
    n0 = count()
    tries = {"via Seite ohne Knopf": (rT1, {"parent_id": rD, "via": rO}),
             "andere Vorlage": (ref(pa, geheim), {"parent_id": rD, "via": rS}),
             "anderes Ziel": (rT1, {"parent_id": ref(pa, tdb), "via": rS}),
             "Ziel als ID von B": (rT1, {"parent_id": db, "via": rS}),
             "via eines anderen Servers": (rT1, {"parent_id": rD, "via": ref(pa + 7, seite)}),
             "via positiv": (rT1, {"parent_id": rD, "via": seite})}
    for name, (tp, b) in tries.items():
        s, d = api("b", "fb-bert", "POST", f"/api/notes/{tp}/duplicate", b)
        ok(f"B: {name} -> nichts angelegt ({s})", s in (403, 404) and count() == n0)
    s, d = api("b", "fb-olga", "POST", f"/api/notes/{rT1}/duplicate", {"parent_id": rD, "via": rS})
    ok(f"B: Olga (nur Lesen) legt nichts an ({s})", s in (403, 404) and count() == n0)
    # fix27 E1: ohne via geht es jetzt fuer Vorlagen DIESER Datenbank (Bert darf sie bearbeiten), nicht fuer die private
    s, d = api("b", "fb-bert", "POST", f"/api/notes/{rT2}/duplicate", {"parent_id": rD})
    ok(f"B: private Vorlage ohne via -> nichts angelegt ({s})", s in (403, 404) and count() == n0)
    s, d = api("b", "fb-bert", "POST", f"/api/notes/{rT1}/duplicate", {"parent_id": rD})
    ok(f"B: Vorlage der Datenbank ohne via (fix27 E1) -> angelegt ({s})", s == 200 and count() == n0 + 1)
    n0 = count()
    s, d = api("b", "fb-bert", "POST", f"/api/notes/{rT1}/duplicate", {"parent_id": rD, "via": rS, "template": True})
    nid3 = d.get("id") if isinstance(d, dict) else None
    ok(f"B: 'template' wird ignoriert ({s})", s == 200 and nid3 and a_row((-nid3 - pa) // 1024)[5] == 0)

    # Bert speichert die Seite (wie der Editor: Inhalt so, wie er ihn bekam) -> auf A wieder A-IDs, Signatur gilt weiter
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rS}")
    doc = json.loads(d["body"])
    doc["blocks"].append({"type": "paragraph", "data": {"text": "von Bert"}})
    s, _ = api("b", "fb-bert", "PATCH", f"/api/notes/{rS}", {"body": json.dumps(doc), "format": "blocks"})
    abts = buttons_of(sql("a", "SELECT body FROM note WHERE id=?", (seite,))[0][0])
    ok(f"B: Seite gespeichert, auf A wieder IDs von A ({s})", s == 200 and len(abts) == 4 and abts[0].get("target") == db
       and abts[0].get("tpl") == t1 and abts[1].get("tpl") == t2 and abts[3].get("target") == db)
    s, d = api("b", "fb-bert", "POST", f"/api/notes/{rT2}/duplicate", {"parent_id": rD, "via": rS})
    ok(f"B: nach Berts Speichern geht die Schaltflaeche weiter ({s})", s == 200)
    # Bert baut eine neue Vorlage ein (geheime Vorlage) -> A entfernt die Vorlage, via wirkt nicht
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rS}")
    doc = json.loads(d["body"])
    doc["blocks"].append({"type": "buttons", "data": {"buttons": [{"label": "Klau", "action": "db", "target": rD, "tpl": ref(pa, geheim)}]}})
    s, _ = api("b", "fb-bert", "PATCH", f"/api/notes/{rS}", {"body": json.dumps(doc), "format": "blocks"})
    n0 = count()
    s2, _ = api("b", "fb-bert", "POST", f"/api/notes/{ref(pa, geheim)}/duplicate", {"parent_id": rD, "via": rS})
    ok(f"B: von Bert eingebaute fremde Vorlage wirkt nicht ({s}, {s2})", s == 200 and s2 in (403, 404) and count() == n0
       and not any(b.get("tpl") == geheim for b in buttons_of(sql("a", "SELECT body FROM note WHERE id=?", (seite,))[0][0])))
    # Gegenstelle ohne tplbtn (aeltere 2.5): verstaendlicher Hinweis statt "Nicht gefunden", nichts angelegt
    caps = json.loads(sql("b", "SELECT caps FROM fed_peer")[0][0])
    sql("b", "UPDATE fed_peer SET caps=?, caps_at=? WHERE id=?", (json.dumps([x for x in caps if x != "tplbtn"]), time.time(), pb))
    n0 = count()
    s, d = api("b", "fb-bert", "POST", f"/api/notes/{rT1}/duplicate", {"parent_id": rD, "via": rS})
    ok(f"B: A ohne tplbtn -> Hinweis 'kann das noch nicht' ({s}, {str(d)[:70]})", s == 404 and HA in str(d.get("error", "")) and count() == n0)
    sql("b", "UPDATE fed_peer SET caps=?, caps_at=? WHERE id=?", (json.dumps(caps), time.time(), pb))
    # Eigene Seite auf B mit Vorlage (lokal) laeuft wie bisher
    s, d = api("b", "fb-bert", "POST", "/api/notes", {"title": "Berts DB", "kind": "database"})
    bdb = d["id"]
    bt = api("b", "fb-bert", "POST", "/api/notes", {"title": "Berts Vorlage", "parent_id": bdb})[1]["id"]
    api("b", "fb-bert", "PATCH", f"/api/notes/{bt}", {"template": True})
    s, d = api("b", "fb-bert", "POST", f"/api/notes/{bt}/duplicate", {"parent_id": bdb, "via": 12345})
    ok(f"B lokal: eigene Vorlage per Schaltflaeche (via wird beim Eigentuemer ignoriert) ({s})", s == 200 and d.get("id", 0) > 0)


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
    print(f"\nFOEDERATION-VORLAGEN {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    run()
