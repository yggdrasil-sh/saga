"""2.5 P3-E3: Gleichzeitig bearbeiten ueber Servergrenzen - zwei eigene Saga-Instanzen A und B (Geruest wie
tests/foederation2.py). Anna (A) teilt eine Seite mit Bert (B, Bearbeiten); Olga (B) und Carl (A) haben keinen Zugriff.

Geprueft:
- Faehigkeiten merge1/presence2 (remote.caps).
- merge1: Anna (lokal) und Bert (fremd, ueber sein Heim) aendern gleichzeitig verschiedene Bloecke -> zusammengefuehrt
  beim Eigentuemer, Antwort mit merged + Inhalt (umgeschrieben wie GET); base "sent" und bases ueber die Grenze;
  gleiche Stelle -> Konfliktblock, dessen Metadaten nur Prinzipal/Handle und Anzeigenamen tragen (kein Anmeldename);
  Basis fehlt -> 409; Gegenstelle ohne merge1 (je Richtung) -> bisheriger 409-Weg; GET ?since ueber die Grenze.
- presence2: Block-Fokus in beide Richtungen (Antwort und Live-Ereignis), Farbe aus der Kennung, Drossel (Blockwechsel
  hoechstens 1x/s, der juengste kommt nach), nur an Personen mit Zugriff, ohne presence2 kein Block.
Ports: FEDMERGE_PORTS="a,a-admin,b,b-admin" (Standard 8370,8371,8372,8373). Raeumt auf (Datenbanken geloescht)."""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foederation as T  # noqa: E402
import foederation2 as F2  # noqa: E402
from foederation_teilen import Stream  # noqa: E402

pa_, aa_, pb_, ab_ = (int(x) for x in os.environ.get("FEDMERGE_PORTS", "8370,8371,8372,8373").split(","))
T.I["a"].update(port=pa_, admin=aa_, db="saga_fmga", url=f"http://127.0.0.1:{pa_}", host=f"127.0.0.1:{pa_}", state="/tmp/fedmerge-a",
                allow=f"localhost:{pb_}")
T.I["b"].update(port=pb_, admin=ab_, db="saga_fmgb", url=f"http://localhost:{pb_}", host=f"localhost:{pb_}", state="/tmp/fedmerge-b",
                allow=f"127.0.0.1:{pa_}")
HA, HB = T.I["a"]["host"], T.I["b"]["host"]
api, sql, wait = T.api, T.sql, T.wait
PALETTE = ("gray", "brown", "orange", "yellow", "green", "blue", "purple", "pink", "red")
STREAM_I = T.I        # foederation_teilen.Stream liest T.I (gleiches Modul)


def ok(n, c, info=None):
    T.ok(n if c or info is None else f"{n} -> {str(info)[:400]}", c)


def ref(pid, rid):
    return -(rid * 1024 + pid)


def P(i, t):
    return {"id": i, "type": "paragraph", "data": {"text": t}}


def doc(*b):
    return json.dumps({"blocks": list(b)})


def texts(d):
    try:
        return [(b.get("id"), b["data"].get("text")) for b in json.loads(d.get("body") or "{}").get("blocks") or []]
    except (ValueError, AttributeError):
        return None


def caps_set(k, pid, drop=(), add=()):
    """Faehigkeiten der Gegenstelle in fed_peer (Instanz k) aendern -> alte Liste (zum Zuruecksetzen)."""
    caps = json.loads(sql(k, "SELECT caps FROM fed_peer")[0][0])
    sql(k, "UPDATE fed_peer SET caps=?, caps_at=? WHERE id=?", (json.dumps([x for x in caps if x not in drop] + list(add)), time.time(), pid))
    return caps


def pcolor(u):
    import hashlib
    return PALETTE[int.from_bytes(hashlib.sha256(u.encode()).digest()[:2], "big") % len(PALETTE)]


def part_merge(pa, pb):
    an, be = "fa-anna", "fb-bert"
    nid = api("a", an, "POST", "/api/notes", {"title": "Gemeinsam"})[1]["id"]
    s, d = api("a", an, "PATCH", f"/api/notes/{nid}", {"format": "blocks", "body": doc(P("b1", "Erster Absatz"), P("b2", "Zweiter Absatz"),
                                                                                       P("b3", "Dritter Absatz"))})
    ok(f"A: Seite angelegt ({s})", s == 200)
    s, _ = api("a", an, "PUT", f"/api/notes/{nid}/shares", {"type": "remote", "target": f"@bert@{HB}", "role": "edit"})
    ok(f"A: mit Bert@B geteilt (Bearbeiten) ({s})", s == 200)
    ok("B: Freigabe angekommen", wait(lambda: len(sql("b", "SELECT 1 FROM fed_inbound")) >= 1, 20))
    r = ref(pa, nid)
    s, d = api("b", be, "GET", f"/api/notes/{r}")
    caps = (d.get("remote") or {}).get("caps", [])
    ok(f"B: Seite lesen, Faehigkeiten merge1 + presence2 ({s}, {caps})", s == 200 and "merge1" in caps and "presence2" in caps)
    r0 = d.get("rev")

    # 1) gleichzeitig verschiedene Bloecke: Anna lokal, Bert ueber sein Heim mit veralteter rev
    s, d = api("a", an, "PATCH", f"/api/notes/{nid}", {"body": doc(P("b1", "Erster Absatz (Anna)"), P("b2", "Zweiter Absatz"),
                                                                  P("b3", "Dritter Absatz")), "format": "blocks", "rev": r0, "merge": 1})
    ok(f"A: Anna speichert lokal ({s})", s == 200 and "merged" not in d)
    s, d = api("b", be, "PATCH", f"/api/notes/{r}", {"body": doc(P("b1", "Erster Absatz"), P("b2", "Zweiter Absatz (Bert)"),
                                                                P("b3", "Dritter Absatz")), "format": "blocks", "rev": r0, "merge": 1,
                                                    "title": "Gemeinsam (B)"})
    ok(f"B: Bert mit veralteter rev -> zusammengefuehrt ({s}, {d.get('merged')})",
       s == 200 and texts(d) == [("b1", "Erster Absatz (Anna)"), ("b2", "Zweiter Absatz (Bert)"), ("b3", "Dritter Absatz")]
       and d.get("merged", {}).get("from") == r0 and d.get("rev") == r0 + 2 and d.get("title") == "Gemeinsam (B)", d)
    ok("B: Antwort nur mit bekannten Feldern", set(d) <= {"ok", "updated", "rev", "merged", "body", "format", "title", "srev"}, sorted(d))
    a_now = api("a", an, "GET", f"/api/notes/{nid}")[1]
    ok("A: Eigentuemer hat den zusammengefuehrten Stand", texts(a_now) == texts(d) and a_now.get("rev") == d["rev"])
    rb = d["rev"]

    # 2) base "sent": Bert hat weitergeschrieben, waehrend die Antwort unterwegs war (Anna hat inzwischen b3 geaendert)
    api("a", an, "PATCH", f"/api/notes/{nid}", {"body": doc(P("b1", "Erster Absatz (Anna)"), P("b2", "Zweiter Absatz (Bert)"),
                                                           P("b3", "Dritter Absatz (Anna)"), P("a4", "Neu von Anna")), "format": "blocks", "rev": rb})
    s, d = api("b", be, "PATCH", f"/api/notes/{r}", {"body": doc(P("b1", "Erster Absatz"), P("b2", "Zweiter Absatz (Bert) weiter"),
                                                                P("b3", "Dritter Absatz")), "format": "blocks", "rev": rb, "merge": 1, "base": "sent"})
    ok(f"B: base sent ueber die Grenze - eigene alte Bloecke loeschen nichts ({s}, {texts(d)})",
       s == 200 and texts(d) == [("b1", "Erster Absatz (Anna)"), ("b2", "Zweiter Absatz (Bert) weiter"), ("b3", "Dritter Absatz (Anna)"),
                                 ("a4", "Neu von Anna")], d)
    rc = d["rev"]
    # bases je Block
    api("a", an, "PATCH", f"/api/notes/{nid}", {"body": doc(P("b1", "Erster Absatz (Anna) 2"), P("b2", "Zweiter Absatz (Bert) weiter"),
                                                           P("b3", "Dritter Absatz (Anna)"), P("a4", "Neu von Anna")), "format": "blocks", "rev": rc})
    rd = api("a", an, "GET", f"/api/notes/{nid}")[1]["rev"]
    s, d = api("b", be, "PATCH", f"/api/notes/{r}", {"body": doc(P("b1", "Erster Absatz (Anna) 2"), P("b2", "Zweiter Absatz (Bert) weiter!"),
                                                                P("b3", "Dritter Absatz (Anna)"), P("a4", "Neu von Anna")), "format": "blocks",
                                                    "rev": rd, "merge": 1, "bases": {"b2": rc, "kaputt id!": 1, "b3": "x"}})
    ok(f"B: bases ueber die Grenze (ungueltige Eintraege fallen weg) ({s}, {texts(d)})",
       s == 200 and dict(texts(d) or []).get("b2") == "Zweiter Absatz (Bert) weiter!" and dict(texts(d) or []).get("b1") == "Erster Absatz (Anna) 2", d)

    # 3) GET ?since ueber die Grenze
    re_ = d["rev"]
    api("a", an, "PATCH", f"/api/notes/{nid}", {"body": doc(P("b1", "Erster Absatz (Anna) 2"), P("b2", "Zweiter Absatz (Bert) weiter!"),
                                                           P("b3", "Dritter Absatz (Anna)"), P("a4", "Neu von Anna, geaendert")),
                                               "format": "blocks", "rev": re_})
    s, d = api("b", be, "GET", f"/api/notes/{r}?since={re_}")
    ok(f"B: GET ?since -> nur geaenderte Bloecke ({s}, {d.get('delta')})",
       s == 200 and "body" not in d and d.get("delta") == {"from": re_, "blocks": [{"id": "b1"}, {"id": "b2"}, {"id": "b3"},
                                                                                     P("a4", "Neu von Anna, geaendert")]} and d.get("rev") == re_ + 1, d)

    # 4) gleiche Stelle -> Konfliktblock (Fassung von Bert direkt unter Annas); Metadaten ohne Anmeldename
    cur = api("a", an, "GET", f"/api/notes/{nid}")[1]
    rf = cur["rev"]
    base_blocks = json.loads(cur["body"])["blocks"]
    mine = [dict(b, data=dict(b["data"], text="Erster Absatz von Bert")) if b["id"] == "b1" else b for b in base_blocks]
    theirs = [dict(b, data=dict(b["data"], text="Erster Absatz von Anna")) if b["id"] == "b1" else b for b in base_blocks]
    api("a", an, "PATCH", f"/api/notes/{nid}", {"body": json.dumps({"blocks": theirs}), "format": "blocks", "rev": rf})
    s, d = api("b", be, "PATCH", f"/api/notes/{r}", {"body": json.dumps({"blocks": mine}), "format": "blocks", "rev": rf, "merge": 1})
    blocks = json.loads(d.get("body") or "{}").get("blocks") or []
    cf = [b for b in blocks if (b.get("tunes") or {}).get("hkconflict")]
    hk = (cf[0]["tunes"]["hkconflict"] if cf else {})
    ok(f"B: Ueberschneidung -> Konfliktblock direkt unter dem Original ({s}, {d.get('merged')})",
       s == 200 and len(cf) == 1 and d["merged"]["conflicts"] == [cf[0]["id"]] and blocks[0]["data"]["text"] == "Erster Absatz von Anna"
       and blocks[1] is cf[0] and cf[0]["data"]["text"] == "Erster Absatz von Bert" and hk.get("of") == "b1", d)
    ok(f"Konfliktblock: by = Prinzipal von Bert, name = Anzeigename ({hk})", hk.get("by") == f"@bert@{HB}" and "Bert Gast" in (hk.get("name") or ""))
    rawa = sql("a", "SELECT body FROM note WHERE id=?", (nid,))[0][0]
    ok("A: kein Anmeldename von Bert im gespeicherten Inhalt", "fb-bert" not in rawa and "fb-bert" not in json.dumps(d))
    vers = sql("a", "SELECT count(*) FROM note_version WHERE note_id=?", (nid,))[0][0]
    ok(f"A: vorher Version gesichert ({vers})", vers >= 1)
    # umgekehrt: Anna (lokal) im Konflikt mit Bert -> Bert sieht Annas Fassung ohne ihren Anmeldenamen
    cur = api("b", be, "GET", f"/api/notes/{r}")[1]
    rg = cur["rev"]
    bb = [b for b in json.loads(cur["body"])["blocks"] if not (b.get("tunes") or {}).get("hkconflict")]
    api("b", be, "PATCH", f"/api/notes/{r}", {"body": json.dumps({"blocks": [dict(b, data=dict(b["data"], text="Dritter von Bert"))
                                                                              if b["id"] == "b3" else b for b in bb]}), "format": "blocks", "rev": rg})
    aa = json.loads(api("a", an, "GET", f"/api/notes/{nid}")[1]["body"])["blocks"]
    s, d = api("a", an, "PATCH", f"/api/notes/{nid}", {"body": json.dumps({"blocks": [dict(b, data=dict(b["data"], text="Dritter von Anna"))
                                                                                       if b["id"] == "b3" else b for b in aa
                                                                                       if not (b.get("tunes") or {}).get("hkconflict")]}),
                                                       "format": "blocks", "rev": rg, "merge": 1})
    s, d = api("b", be, "GET", f"/api/notes/{r}")
    cfb = [b["tunes"]["hkconflict"] for b in json.loads(d.get("body") or "{}").get("blocks") or [] if (b.get("tunes") or {}).get("hkconflict")]
    ok(f"B: Annas Konfliktfassung ohne Anmeldenamen ({cfb})", s == 200 and cfb and all("fa-anna" not in json.dumps(x) for x in cfb)
       and any((x.get("by") or "").startswith("~") and "Anna" in (x.get("name") or "") for x in cfb))

    # 5) Basis fehlt -> 409 wie bisher
    cur = api("a", an, "GET", f"/api/notes/{nid}")[1]
    sql("a", "DELETE FROM note_base WHERE note_id=?", (nid,))
    api("a", an, "PATCH", f"/api/notes/{nid}", {"body": cur["body"].replace("Dritter", "Dritter!"), "format": "blocks", "rev": cur["rev"]})
    s, d = api("b", be, "PATCH", f"/api/notes/{r}", {"body": doc(P("b1", "egal")), "format": "blocks", "rev": cur["rev"] - 1, "merge": 1})
    ok(f"B: Basis beim Eigentuemer weg -> 409 mit rev ({s}, {d})", s == 409 and isinstance(d.get("rev"), int))
    s, d = api("b", be, "GET", f"/api/notes/{r}?since={cur['rev'] - 1}")
    ok(f"B: GET ?since mit fehlender Basis -> ganzer Inhalt ({s})", s == 200 and "body" in d and "delta" not in d)

    # 6) Gegenstellen ohne merge1 -> bisheriger 409-Weg (je Richtung)
    cur = api("a", an, "GET", f"/api/notes/{nid}")[1]
    api("a", an, "PATCH", f"/api/notes/{nid}", {"body": cur["body"].replace("Erster", "Erster:"), "format": "blocks", "rev": cur["rev"]})
    old_b = caps_set("b", pb, drop=("merge1",))          # B glaubt, A koenne kein merge1 (aelterer Eigentuemer)
    s, d = api("b", be, "GET", f"/api/notes/{r}")
    ok("B: A ohne merge1 -> caps ohne merge1 (Web nimmt den 409-Weg)", "merge1" not in (d.get("remote") or {}).get("caps", []))
    s, d = api("b", be, "PATCH", f"/api/notes/{r}", {"body": cur["body"].replace("Zweiter", "Zweiter:"), "format": "blocks", "rev": cur["rev"], "merge": 1})
    ok(f"B: Eigentuemer ohne merge1 -> 409 (merge nicht weitergereicht) ({s})", s == 409)
    sql("b", "UPDATE fed_peer SET caps=?, caps_at=? WHERE id=?", (json.dumps(old_b), time.time(), pb))
    old_a = caps_set("a", pa, drop=("merge1",))          # A glaubt, B koenne kein merge1 (aelteres Heim)
    s, d = api("b", be, "PATCH", f"/api/notes/{r}", {"body": cur["body"].replace("Zweiter", "Zweiter:"), "format": "blocks", "rev": cur["rev"], "merge": 1})
    ok(f"A: Heim ohne merge1 -> merge-Felder verworfen, 409 ({s})", s == 409)
    sql("a", "UPDATE fed_peer SET caps=?, caps_at=? WHERE id=?", (json.dumps(old_a), time.time(), pa))
    s, d = api("b", be, "PATCH", f"/api/notes/{r}", {"body": cur["body"].replace("Zweiter", "Zweiter:"), "format": "blocks", "rev": cur["rev"], "merge": 1})
    ok(f"B: beide wieder mit merge1 -> zusammengefuehrt ({s}, {texts(d)})", s == 200 and "merged" in d and "Erster:" in d.get("body", "") and "Zweiter:" in d.get("body", ""))
    return nid, r


def part_presence(pa, pb, nid, r):
    an, be = "fa-anna", "fb-bert"
    sb, sa, so = Stream("b", be), Stream("a", an), Stream("b", "fb-olga")
    try:
        time.sleep(0.5)
        s, d = api("b", be, "POST", "/api/presence", {"note_id": r, "editing": True, "block": "b2"})
        me = [u for u in d.get("users", []) if u.get("me")]
        ok(f"B: Anwesenheit mit Block ({s}, {me})", s == 200 and me and me[0].get("block") == "b2" and me[0].get("color") in PALETTE)
        s, d = api("a", an, "POST", "/api/presence", {"note_id": nid, "editing": True, "block": "b1"})
        bert = [u for u in d.get("users", []) if u.get("remote")]
        ok(f"A: Anna sieht Bert in Block b2, Farbe aus seiner Kennung ({bert})", s == 200 and bert and bert[0].get("block") == "b2"
           and bert[0].get("color") == pcolor(f"@bert@{HB}"))
        ok("A: kein Anmeldename in der Anwesenheit", "fb-bert" not in json.dumps(d))
        ev = sb.wait("presence", lambda x: x.get("note_id") == r and any(not u.get("me") and u.get("block") == "b1" for u in x.get("users", [])))
        anna = [u for u in (ev or {}).get("users", []) if not u.get("me")]
        ok(f"B: Live: Anna in Block b1 mit Farbe ({anna})", bool(ev) and anna[0].get("color") in PALETTE and "Anna" in anna[0].get("name", ""))
        ok("B: kein Anmeldename von Anna im Ereignis", ev is not None and "fa-anna" not in json.dumps(ev))
        # Drossel: Blockwechsel schnell hintereinander -> zweiter "skipped", der juengste kommt trotzdem beim Eigentuemer an
        time.sleep(1.1)
        s1, d1 = api("b", be, "POST", "/api/presence", {"note_id": r, "editing": True, "block": "b3"})
        s2, d2 = api("b", be, "POST", "/api/presence", {"note_id": r, "editing": True, "block": "a4"})
        ok(f"B: Blockwechsel gedrosselt (zweite Meldung skipped) ({s1}, {s2}, {d2})", s1 == 200 and s2 == 200 and d2.get("skipped") is True)

        def blk_at_a():
            time.sleep(0.3)
            d = api("a", an, "POST", "/api/presence", {"note_id": nid, "editing": True, "block": "b1"})[1]
            return [u.get("block") for u in d.get("users", []) if u.get("remote")] == ["a4"]
        ok("A: juengster Block von Bert kommt nach der Drossel an", wait(blk_at_a, 6, 0.6))
        ev = sa.wait("presence", lambda x: x.get("note_id") == nid and any(u.get("remote") and u.get("block") == "a4" for u in x.get("users", [])))
        ok("A: Live-Ereignis mit Berts Block", bool(ev))
        # Olga (B) und Carl (A) ohne Zugriff: keine Anwesenheit, keine Ereignisse
        s, d = api("b", "fb-olga", "POST", "/api/presence", {"note_id": r, "editing": True, "block": "b1"})
        ok(f"B: Olga ohne Freigabe -> keine Anwesenden ({s})", s != 200 or not d.get("users"), d)
        s, d = api("a", "fa-carl", "POST", "/api/presence", {"note_id": nid, "editing": True})
        ok(f"A: Carl ohne Zugriff -> 404 ({s})", s == 404)
        time.sleep(0.5)
        ok("B: Olga bekommt keine presence-Ereignisse der Seite", not [e for e in so.ev if e[0] == "presence" and e[1].get("note_id") == r])
        # ohne presence2 (B als aelteres Heim aus Sicht von A): kein Block in beide Richtungen
        old_a = caps_set("a", pa, drop=("presence2",))
        time.sleep(1.1)
        mark = len(sb.ev)
        api("b", be, "POST", "/api/presence", {"note_id": r, "editing": True, "block": "b1"})
        time.sleep(1.1)
        s, d = api("a", an, "POST", "/api/presence", {"note_id": nid, "editing": True, "block": "b3"})
        bert = [u for u in d.get("users", []) if u.get("remote")]
        ok(f"A: Heim ohne presence2 -> Berts Block wird nicht uebernommen ({bert})", bert and bert[0].get("block") in (None, "a4"))
        ev = sb.wait("presence", lambda x: x.get("note_id") == r, mark, timeout=4)
        anna = [u for u in (ev or {}).get("users", []) if not u.get("me")]
        ok(f"B: ohne presence2 kommt Annas Block nicht mit, Farbe setzt das Heim ({anna})", bool(ev) and anna and anna[0].get("block") is None
           and anna[0].get("color") in PALETTE)
        sql("a", "UPDATE fed_peer SET caps=?, caps_at=? WHERE id=?", (json.dumps(old_a), time.time(), pa))
        api("b", be, "POST", "/api/presence", {"note_id": r, "leave": True})
    finally:
        for st in (sb, sa, so):
            st.close()


def run():
    try:
        A, B, pa, pb = F2.main()
        nid, r = part_merge(pa, pb)
        part_presence(pa, pb, nid, r)
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
        if not os.environ.get("FEDMERGE_KEEP"):
            for k in T.I:
                try:
                    T.pg_admin(f"DROP DATABASE IF EXISTS {T.I[k]['db']} WITH (FORCE)")
                except Exception as ex:  # noqa: BLE001
                    print("aufraeumen:", ex)
    res = T.res
    print(f"\nFOEDERATION-MERGE {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    run()
