"""Bughunt W02/W03 (2.5): Personen anderer Saga-Server mit stabilem Handle - zwei eigene Instanzen A und B (eigene Datenbanken
und Ports, wie tests/foederation_teilen.py).

W02: Anna (A) teilt per Handle ('~x@B') mit Bert, der auf B einen oeffentlichen Namen hat. Die Freigabe kommt an ('Mit mir
geteilt', Benachrichtigung), Bert oeffnet die Seite, Anna aendert die Rolle und entzieht - ueber den Handle wie ueber den
Namen. A fuehrt Handle und Namen zu einer Person zusammen (Faehigkeit rename, actor.handle).
W03: Bert benennt sich auf B um -> person.renamed statt person.gone: Freigabe, Kommentar und Anzeigename bleiben auf beiden
Seiten (A schreibt auf den neuen Namen um). Ebenso, wenn Anna (Eigentuemerin, A) sich umbenennt (Eingangs-Verweise auf B) und
wenn eine Person ohne oeffentlichen Namen erstmals einen setzt (Handle -> Name).
Alte Gegenstelle (ohne rename, nachgestellt ueber die gespeicherten Faehigkeiten): wie bisher person.gone, die Freigabe endet;
das Konto meldet vorher, was verloren geht (fed_loss im Umbenennen-Dialog).
Raeumt auf (Datenbanken geloescht)."""
import json, os, sys, time, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foederation as T  # noqa: E402
import foederation2 as F2  # noqa: E402

T.I["a"].update(port=8395, admin=8394, db="saga_fta", url="http://127.0.0.1:8395", host="127.0.0.1:8395", state="/tmp/fedhandle-a", allow="localhost:8397")
T.I["b"].update(port=8397, admin=8396, db="saga_ftb", url="http://localhost:8397", host="localhost:8397", state="/tmp/fedhandle-b", allow="127.0.0.1:8395")
HA, HB = T.I["a"]["host"], T.I["b"]["host"]
ok, wait, api = T.ok, T.wait, T.api


def sql(k, q, args=()):
    return [tuple(r) for r in T.sql(k, q, args)]


def ref(pid, rid):
    return -(rid * 1024 + pid)


def handle_of(k, uid):
    return api(k, uid, "GET", "/api/account/federation")[1].get("handle")


def shared_ids(k, uid):
    s, d = api(k, uid, "GET", "/api/fed/shared")
    return {x["id"]: x for x in d.get("items", [])} if s == 200 else {}


def remote_shares(nid):
    return sorted((r[0], r[1]) for r in sql("a", "SELECT target, role FROM note_share WHERE note_id=? AND target_type='remote'", (nid,)))


def page(title, text="Inhalt"):
    s, d = api("a", "fa-anna", "POST", "/api/notes", {"title": title})
    api("a", "fa-anna", "PATCH", f"/api/notes/{d['id']}", {"body": T.blocks(T.P(text, "b1")), "format": "blocks"})
    return d["id"]


def share(nid, target, role):
    return api("a", "fa-anna", "PUT", f"/api/notes/{nid}/shares", {"type": "remote", "target": target, "role": role})


def notes_kind(k, uid, kind):
    return [n for n in api(k, uid, "GET", "/api/notifications")[1].get("items", []) if n.get("kind") == kind]


# =====================================================================================================
def part_w02(pa, pb):
    ok("A und B melden die Faehigkeit rename", wait(lambda: "rename" in json.loads(sql("a", "SELECT caps FROM fed_peer")[0][0])
                                                     and "rename" in json.loads(sql("b", "SELECT caps FROM fed_peer")[0][0]), 20))
    hb = handle_of("b", "fb-bert")
    ok(f"B: Bert hat einen Handle ({hb}) und den oeffentlichen Namen bert", bool(hb) and hb.startswith("~"))
    hname = hb.split("@")[0]
    P = page("W Handle")
    s, d = share(P, "@" + hb, "view")
    ok(f"A: Seite per Handle mit Bert geteilt ({s})", s == 200)
    ok("B: Freigabe angekommen (fed_inbound)", wait(lambda: sql("b", "SELECT 1 FROM fed_inbound WHERE target='fb-bert' AND note_id=?", (P,)), 15))
    ok("B: Benachrichtigung fuer Bert", wait(lambda: notes_kind("b", "fb-bert", "share.added"), 10))
    rP = ref(pa, P)
    ids = shared_ids("b", "fb-bert")
    ok(f"B: 'Mit mir geteilt' zeigt die Seite (W02, {len(ids)})", rP in ids and ids[rP].get("title") == "W Handle")
    ok(f"A: Handle und Name zusammengefuehrt - Freigabe jetzt unter @bert@B ({remote_shares(P)})", remote_shares(P) == [(f"@bert@{HB}", "view")])
    ok("A: Zuordnung Handle -> Name gemerkt", sql("a", "SELECT principal FROM fed_alias WHERE handle=?", (hname,)) == [(f"@bert@{HB}",)])
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rP}")
    ok(f"B: Bert oeffnet die Seite ({s})", s == 200 and d.get("title") == "W Handle" and d.get("role") == "view")
    s, d = api("b", "fb-bert", "PATCH", f"/api/notes/{rP}", {"title": "nein"})
    ok(f"B: Lesen - Bearbeiten abgewiesen ({s})", s == 403)
    # Rolle aendern - ueber den Handle (dieselbe Person, keine zweite Freigabe) und ueber den Namen
    s, d = share(P, "@" + hb, "comment")
    ok(f"A: Rolle per Handle geaendert ({s}, {remote_shares(P)})", s == 200 and remote_shares(P) == [(f"@bert@{HB}", "comment")])
    s, d = share(P, f"@bert@{HB}", "edit")
    ok(f"A: Rolle per Name geaendert ({s}, {remote_shares(P)})", s == 200 and remote_shares(P) == [(f"@bert@{HB}", "edit")])
    ok("B: Rolle angekommen", wait(lambda: sql("b", "SELECT role FROM fed_inbound WHERE target='fb-bert' AND note_id=?", (P,)) == [("edit",)], 15))
    s, d = api("b", "fb-bert", "PATCH", f"/api/notes/{rP}", {"title": "W Handle (B)"})
    ok(f"B: Bert bearbeitet ({s})", s == 200 and sql("a", "SELECT title FROM note WHERE id=?", (P,))[0][0] == "W Handle (B)")
    s, d = api("b", "fb-bert", "POST", f"/api/notes/{rP}/comments", {"block_id": "b1", "body": "Kommentar per Handle"})
    ok(f"B: Bert kommentiert ({s})", s == 200 and sql("a", "SELECT uid FROM note_comment WHERE note_id=?", (P,)) == [(f"@bert@{HB}",)])
    s, d = api("a", "fa-anna", "GET", f"/api/notes/{P}/shares")
    rs = [x for x in d.get("shares", []) if x.get("type") == "remote"]
    ok(f"A: Freigabeliste zeigt eine Person ({[x.get('target') for x in rs]})", len(rs) == 1 and rs[0]["target"] == f"@bert@{HB}")
    # Entziehen ueber den Handle
    s, d = api("a", "fa-anna", "DELETE", f"/api/notes/{P}/shares?type=remote&target=" + urllib.parse.quote("@" + hb))
    ok(f"A: per Handle entzogen ({s}, {d.get('removed')})", s == 200 and d.get("removed") is True and remote_shares(P) == [])
    ok("B: Freigabe weg", wait(lambda: not sql("b", "SELECT 1 FROM fed_inbound WHERE target='fb-bert' AND note_id=?", (P,)), 15))
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rP}")
    ok(f"B: Seite nicht mehr lesbar ({s})", s in (403, 404))
    # Neu teilen per Handle: A kennt die Person schon -> gleich unter dem Namen
    P2 = page("W Handle 2")
    s, d = share(P2, "@" + hb, "view")
    ok(f"A: erneut per Handle geteilt -> gleich unter @bert@B ({s}, {remote_shares(P2)})", s == 200 and remote_shares(P2) == [(f"@bert@{HB}", "view")])
    ok("B: angekommen", wait(lambda: ref(pa, P2) in shared_ids("b", "fb-bert"), 15))


def part_w03(pa, pb):
    # Bert (B) bekommt eine Seite mit Kommentar; Bert teilt selbst eine Seite mit Anna (Gegenrichtung)
    P = page("W Umbenennen")
    s, _ = share(P, f"@bert@{HB}", "comment")
    rP = ref(pa, P)
    ok(f"A: an @bert@B geteilt ({s})", s == 200 and wait(lambda: rP in shared_ids("b", "fb-bert"), 15))
    s, _ = api("b", "fb-bert", "POST", f"/api/notes/{rP}/comments", {"block_id": "b1", "body": "vor dem Umbenennen"})
    ok(f"B: Bert kommentiert ({s})", s == 200)
    bn = api("b", "fb-bert", "POST", "/api/notes", {"title": "Berts Seite"})[1]["id"]
    s, _ = api("b", "fb-bert", "PUT", f"/api/notes/{bn}/shares", {"type": "remote", "target": f"@anna@{HA}", "role": "edit"})
    rB = ref(pb, bn)
    ok(f"B: Bert teilt mit @anna@A ({s})", s == 200 and wait(lambda: rB in shared_ids("a", "fa-anna"), 15))
    s, d = api("b", "fb-bert", "GET", "/api/account/public-name")
    ok(f"B: nichts geht verloren (A kann rename) ({d.get('fed_loss')})", s == 200 and d.get("fed_loss") == {"n": 0, "hosts": []})
    # --- Bert benennt sich um ------------------------------------------------------------------------------------------------
    s, d = api("b", "fb-bert", "PUT", "/api/account/public-name", {"name": "bertneu"})
    ok(f"B: Bert heisst jetzt bertneu ({s})", s == 200 and d.get("name") == "bertneu")
    ok("A: Freigabe auf den neuen Namen umgeschrieben (W03)", wait(lambda: remote_shares(P) == [(f"@bertneu@{HB}", "comment")], 15))
    ok("A: keine Freigabe mehr an den alten Namen", not sql("a", "SELECT 1 FROM note_share WHERE target=?", (f"@bert@{HB}",)))
    ok("A: Kommentar gehoert bertneu", sql("a", "SELECT uid FROM note_comment WHERE note_id=?", (P,)) == [(f"@bertneu@{HB}",)])
    ok("A: Anzeigename unter dem neuen Namen", sql("a", "SELECT display FROM fed_person WHERE principal=?", (f"@bertneu@{HB}",)) == [("Bert Gast",)])
    ok("A: Pruefprotokoll person.renamed", any("bert -> bertneu" in r[0] for r in sql("a", "SELECT detail FROM fed_log WHERE event='person.renamed'")))
    ok("A: kein person.gone", not sql("a", "SELECT 1 FROM fed_log WHERE event='person.gone'"))
    ok("B: Eingangs-Verweis bleibt", bool(sql("b", "SELECT 1 FROM fed_inbound WHERE target='fb-bert' AND note_id=?", (P,))))
    ids = shared_ids("b", "fb-bert")
    ok(f"B: 'Mit mir geteilt' zeigt die Seite weiter ({len(ids)})", rP in ids)
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rP}")
    ok(f"B: Seite oeffnen nach dem Umbenennen ({s})", s == 200 and d.get("role") == "comment")
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rP}/comments")
    mine = [c for th in d.get("threads", []) for c in th.get("comments", []) if (c.get("author") or {}).get("me")]
    ok(f"B: eigener Kommentar weiter als eigener erkannt ({s}, {len(mine)})", s == 200 and len(mine) == 1)
    s, d = share(P, f"@bertneu@{HB}", "edit")
    ok(f"A: Rolle per neuem Namen ({s})", s == 200 and wait(lambda: sql("b", "SELECT role FROM fed_inbound WHERE target='fb-bert' AND note_id=?", (P,)) == [("edit",)], 15))
    s, d = share(P, f"@bert@{HB}", "view")
    ok(f"A: alter Name ist nicht mehr teilbar ({s})", s == 400)
    s, d = api("b", "fb-anna2", "PUT", "/api/account/public-name", {"name": "bert"})
    ok(f"B: alter Name bleibt fuer andere Konten gesperrt ({s})", s == 409)
    s, d = api("b", "fb-bert", "PUT", "/api/account/public-name", {"name": "bert"})
    ok(f"B: Bert nimmt seinen frueheren Namen wieder ({s})", s == 200)
    ok("A: wieder @bert@B", wait(lambda: remote_shares(P) == [(f"@bert@{HB}", "edit")], 15))
    s, d = api("b", "fb-bert", "PUT", "/api/account/public-name", {"name": "bertneu"})
    ok(f"A: und wieder @bertneu@B ({s})", s == 200 and wait(lambda: remote_shares(P) == [(f"@bertneu@{HB}", "edit")], 15))
    # --- Anna (Eigentuemerin auf A) benennt sich um: Eingangs-Verweise auf B und Berts Freigabe an Anna bleiben ---------------
    s, d = api("a", "fa-anna", "PUT", "/api/account/public-name", {"name": "annaneu"})
    ok(f"A: Anna heisst jetzt annaneu ({s})", s == 200)
    ok("B: Berts Freigabe an Anna umgeschrieben", wait(lambda: sql("b", "SELECT target FROM note_share WHERE note_id=? AND target_type='remote'", (bn,))
                                                       == [(f"@annaneu@{HA}",)], 15))
    ok("B: Eigentuemer der Eingangs-Verweise umgeschrieben",
       wait(lambda: {r[0] for r in sql("b", "SELECT owner FROM fed_inbound")} == {f"@annaneu@{HA}"}, 15))
    ok("A: Eingangs-Verweis von B bleibt", bool(sql("a", "SELECT 1 FROM fed_inbound WHERE target='fa-anna' AND note_id=?", (bn,))))
    ok("A: 'Mit mir geteilt' zeigt Berts Seite weiter", rB in shared_ids("a", "fa-anna"))
    s, d = api("a", "fa-anna", "GET", f"/api/notes/{rB}")
    ok(f"A: Anna oeffnet Berts Seite ({s})", s == 200 and d.get("role") == "edit")
    ok("B: Bert sieht Annas Seite weiter", rP in shared_ids("b", "fb-bert"))
    # --- erstmals ein oeffentlicher Name (Handle -> Name): Anna-B auf B hatte keinen ------------------------------------------
    ha = handle_of("b", "fb-anna")
    P3 = page("W Erstname")
    s, _ = share(P3, "@" + ha, "view")
    r3 = ref(pa, P3)
    ok(f"A: per Handle an Anna-B (ohne Namen) ({s})", s == 200 and wait(lambda: r3 in shared_ids("b", "fb-anna"), 15))
    ok("A: Freigabe unter dem Handle", remote_shares(P3) == [("@" + ha, "view")])
    s, d = api("b", "fb-anna", "PUT", "/api/account/public-name", {"name": "annab2"})
    ok(f"B: Anna-B setzt erstmals einen Namen ({s})", s == 200)
    ok("A: Freigabe auf den Namen umgeschrieben", wait(lambda: remote_shares(P3) == [(f"@annab2@{HB}", "view")], 15))
    ok("B: Seite weiter sichtbar und lesbar", r3 in shared_ids("b", "fb-anna") and api("b", "fb-anna", "GET", f"/api/notes/{r3}")[0] == 200)


def part_old(pa, pb):
    """Gegenstelle ohne rename (wie aeltere Versionen): B kennt die Faehigkeit bei A nicht -> wie bisher person.gone."""
    P = page("W Alt")
    s, _ = share(P, f"@olga@{HB}", "view")
    rP = ref(pa, P)
    ok(f"A: an @olga@B geteilt ({s})", s == 200 and wait(lambda: rP in shared_ids("b", "fb-olga"), 15))
    caps = [x for x in json.loads(sql("b", "SELECT caps FROM fed_peer")[0][0]) if x != "rename"]
    sql("b", "UPDATE fed_peer SET caps=?, caps_at=? WHERE id=?", (json.dumps(caps), time.time(), pb_peer()))
    s, d = api("b", "fb-olga", "GET", "/api/account/public-name")
    ok(f"B: Umbenennen-Dialog meldet die Freigabe von A ({d.get('fed_loss')})", s == 200 and d.get("fed_loss") == {"n": 1, "hosts": [HA]})
    s, d = api("b", "fb-bert", "GET", "/api/account/public-name")
    ok(f"B: Bert hat ebenso Freigaben von A ({d.get('fed_loss')})", (d.get("fed_loss") or {}).get("n", 0) >= 1)
    n0 = len(sql("a", "SELECT 1 FROM fed_log WHERE event='person.gone'"))
    s, d = api("b", "fb-olga", "PUT", "/api/account/public-name", {"name": "olgax"})
    ok(f"B: Olga benennt sich um ({s})", s == 200)
    ok("A: person.gone wie bisher", wait(lambda: len(sql("a", "SELECT 1 FROM fed_log WHERE event='person.gone'")) > n0, 15))
    ok("A: Freigabe an den alten Namen endet (altes Verhalten)", wait(lambda: remote_shares(P) == [], 10))
    ok("B: Eingangs-Verweis von A weg", not sql("b", "SELECT 1 FROM fed_inbound WHERE target='fb-olga'"))
    ok("B: Dialog meldet danach nichts mehr", api("b", "fb-olga", "GET", "/api/account/public-name")[1].get("fed_loss") == {"n": 0, "hosts": []})
    ok("A: Berts Freigaben (andere Person) unberuehrt", bool(sql("a", "SELECT 1 FROM note_share WHERE target=?", (f"@bertneu@{HB}",))))


def pb_peer():
    return sql("b", "SELECT id FROM fed_peer")[0][0]


def run():
    try:
        A, B, pa, pb = F2.main()
        part_w02(pa, pb)
        part_w03(pa, pb)
        part_old(pa, pb)
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
    print(f"\nFOEDERATION-HANDLES {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    run()
