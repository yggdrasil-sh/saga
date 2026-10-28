"""2.5 (P6-5/P6-6): Erwaehnungen (Faehigkeit ment1) und synchronisierte Bloecke (sync1) ueber Servergrenzen - zwei eigene
Saga-Instanzen A und B (Geruest wie tests/foederation_personen.py).

Anna (A) teilt "Plan" mit Bert auf B (Bearbeiten). Geprueft:
- Faehigkeiten ment1/sync1 im hello (remote.caps).
- Anna erwaehnt Bert (Prinzipal '@bert@B'): A speichert die Erwaehnung, page.mention kommt bei Bert auf B an (Titel, ohne
  Anmeldename); B liest den Inhalt mit Berts eigenem Handle (me) und Anna als '@anna@A' - kein Anmeldename von A.
- Bert erwaehnt Anna ueber das Heim: A speichert Annas lokalen Handle, Anna bekommt page.mention.
- Kommentar von Bert mit "<@@anna@A>": Anna bekommt comment.mention; Kommentarliste auf B mit people.
- Gegenstelle ohne ment1: Erwaehnung als reiner Text, keine Mitteilung.
- Synchronisierte Bloecke: Kopie auf einer mit Bert geteilten Seite - mit Zugriff auf das Original Inhalt + src (note als
  negative ID, role edit), Bert bearbeitet die Kopie ueber das Heim -> Original bei A geaendert; ohne Zugriff auf das
  Original nur noaccess (kein Titel/Inhalt); Gegenstelle ohne sync1 bekommt nur {sid}, ihr Zurueckschreiben aendert nichts.
Ports: FEDM_PORTS="a,a-admin,b,b-admin" (Standard 8350,8351,8352,8353); Zustand: FEDM_STATE=<praefix> (Standard /tmp/fedm).
Raeumt auf (Datenbanken geloescht)."""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SAGA_MENTION_DELAY", "1")              # Mitteilungen nach 1 s statt 20 s (beide Instanzen)
import foederation as T  # noqa: E402
import foederation2 as F2  # noqa: E402

pa_, aa_, pb_, ab_ = (int(x) for x in os.environ.get("FEDM_PORTS", "8350,8351,8352,8353").split(","))
st_ = os.environ.get("FEDM_STATE", "/tmp/fedm")
T.I["a"].update(port=pa_, admin=aa_, db="saga_fmea", url=f"http://127.0.0.1:{pa_}", host=f"127.0.0.1:{pa_}", state=st_ + "-a",
                allow=f"localhost:{pb_}")
T.I["b"].update(port=pb_, admin=ab_, db="saga_fmeb", url=f"http://localhost:{pb_}", host=f"localhost:{pb_}", state=st_ + "-b",
                allow=f"127.0.0.1:{pa_}")
HA, HB = T.I["a"]["host"], T.I["b"]["host"]
api, sql = T.api, T.sql
SECRET = "Geheimer Plantext 7731"
SECRET_T = "Streng geheimes Original"


def ok(n, c, info=None):
    T.ok(n if c or info is None else f"{n} -> {str(info)[:500]}", c)


def ref(pid, rid):
    return -(rid * 1024 + pid)


def local_ref(k, uid):
    return T.py(k, f"from saga.core import local_ref\nprint(json.dumps(local_ref({uid!r})))")


def M(u, txt):
    return f'<span class="hk-mention" data-u="{u}">{txt}</span>'


def P(i, text):
    return {"id": i, "type": "paragraph", "data": {"text": text}}


def doc(blocks):
    return {"body": json.dumps({"blocks": blocks}, ensure_ascii=False), "format": "blocks"}


def blocks_of(d):
    try:
        return json.loads(d.get("body") or '{"blocks": []}')["blocks"]
    except (ValueError, TypeError):
        return []


def texts(body):
    """Alle Absatz-Texte eines Inhalts (JSON) zusammen - fuer Pruefungen auf data-u ohne JSON-Maskierung."""
    try:
        return " ".join(str(b.get("data", {}).get("text", "")) for b in json.loads(body or '{"blocks": []}')["blocks"])
    except (ValueError, TypeError):
        return ""


def synced_of(bl):
    return next((b for b in bl if b.get("type") == "synced"), None)


def notes(k, uid, kind):
    s, d = api(k, uid, "GET", "/api/notifications?limit=50")
    return [x for x in (d.get("items") or []) if x.get("kind") == kind]


def set_caps(pid, drop):
    caps = json.loads(sql("a", "SELECT caps FROM fed_peer WHERE id=?", (pid,))[0][0])
    sql("a", "UPDATE fed_peer SET caps=?, caps_at=? WHERE id=?", (json.dumps([x for x in caps if x not in drop]), time.time(), pid))
    return caps


def part(pa, pb):
    an, be = "fa-anna", "fb-bert"
    anna_a = local_ref("a", an)
    bert_b = local_ref("b", be)
    nid = api("a", an, "POST", "/api/notes", {"title": "Plan"})[1]["id"]
    s, _ = api("a", an, "PUT", f"/api/notes/{nid}/shares", {"type": "remote", "target": f"@bert@{HB}", "role": "edit"})
    ok(f"A: Plan mit Bert@B geteilt (Bearbeiten) ({s})", s == 200)
    ok("B: Freigabe angekommen", T.wait(lambda: len(sql("b", "SELECT 1 FROM fed_inbound")) >= 1, 20))
    rN = ref(pa, nid)
    s, d = api("b", be, "GET", f"/api/notes/{rN}")
    caps = (d.get("remote") or {}).get("caps", [])
    ok(f"B: Faehigkeiten ment1 und sync1 gemeldet ({s})", s == 200 and "ment1" in caps and "sync1" in caps, d.get("remote"))

    # --- Anna erwaehnt Bert ------------------------------------------------------------------------------------------
    s, d = api("a", an, "PATCH", f"/api/notes/{nid}", doc([P("p1", "Hallo " + M(f"@bert@{HB}", "Bert"))]))
    ok(f"A: Erwaehnung von Bert gespeichert ({s})", s == 200, d)
    rows = [r[0] for r in sql("a", "SELECT person FROM note_mention WHERE note_id=?", (nid,))]
    ok("A: note_mention mit Berts Prinzipal", rows == [f"@bert@{HB}"], rows)
    ok("B: page.mention angekommen", T.wait(lambda: notes("b", be, "page.mention"), 25))
    it = (notes("b", be, "page.mention") or [{}])[0]
    raw = json.dumps(it, ensure_ascii=False)
    ok("B: Mitteilung mit Titel und Anna, ohne Anmeldename", it.get("title") == "Plan" and "Anna" in it.get("text", "") and "fa-anna" not in raw, it)
    s, d = api("b", be, "GET", f"/api/notes/{rN}")
    raw = json.dumps(d, ensure_ascii=False)
    pm = d.get("people") or {}
    ok(f"B: Inhalt mit Berts eigenem Handle (me) ({s})", f'data-u="{bert_b}"' in texts(d.get("body")) and (pm.get(bert_b) or {}).get("me") is True, (d.get("body"), pm))

    # --- Bert erwaehnt Anna ueber das Heim ------------------------------------------------------------------------------
    rev = d.get("rev")
    s, d = api("b", be, "PATCH", f"/api/notes/{rN}", dict(doc([P("p1", "Hallo " + M(bert_b, "Bert")), P("p2", "Frage an " + M(f"@anna@{HA}", "Anna"))]), rev=rev))
    ok(f"B: Speichern mit Erwaehnung ueber das Heim ({s})", s == 200, d)
    body = sql("a", "SELECT body FROM note WHERE id=?", (nid,))[0][0]
    ok(f"A: Anna als lokaler Handle ({anna_a}), Bert als Prinzipal gespeichert", f'data-u="{anna_a}"' in texts(body) and f'data-u="@bert@{HB}"' in texts(body), body)
    ok("A: Anna bekommt page.mention (von Bert)", T.wait(lambda: notes("a", an, "page.mention"), 20), notes("a", an, "page.mention"))
    s, d = api("b", be, "GET", f"/api/notes/{rN}")
    raw = json.dumps(d, ensure_ascii=False)
    pm = d.get("people") or {}
    ok(f"B: Anna als Prinzipal '@anna@A' mit Name ({s})", f'data-u="@anna@{HA}"' in texts(d.get("body")) and "Anna" in (pm.get(f"@anna@{HA}") or {}).get("name", ""), pm)
    ok("B: kein Anmeldename und kein Handle von A in der Antwort", "fa-anna" not in raw and anna_a not in raw, raw[:400])

    # --- Kommentar ---------------------------------------------------------------------------------------------------
    s, d = api("b", be, "POST", f"/api/notes/{rN}/comments", {"block_id": "p2", "body": f"Siehe <@@anna@{HA}> bitte"})
    ok(f"B: Kommentar mit Erwaehnung ueber das Heim ({s})", s == 200, d)
    cbody = sql("a", "SELECT body FROM note_comment WHERE note_id=? ORDER BY id DESC LIMIT 1", (nid,))[0][0]
    ok("A: Token als lokaler Handle gespeichert", f"<@{anna_a}>" in cbody, cbody)
    ok("A: Anna bekommt comment.mention", T.wait(lambda: notes("a", an, "comment.mention"), 15))
    s, d = api("b", be, "GET", f"/api/notes/{rN}/comments")
    raw = json.dumps(d, ensure_ascii=False)
    pm = d.get("people") or {}
    ok(f"B: Kommentarliste mit Prinzipal und people ({s})", f"<@@anna@{HA}>" in raw and "Anna" in (pm.get(f"@anna@{HA}") or {}).get("name", ""), (raw[:300], pm))
    ok("B: Kommentarliste ohne Anmeldename/Handle von A", "fa-anna" not in raw and anna_a not in raw, raw[:300])

    # --- Gegenstelle ohne ment1 -----------------------------------------------------------------------------------------
    caps_a = set_caps(pa, ("ment1",))
    s, d = api("b", be, "GET", f"/api/notes/{rN}")
    raw = json.dumps(d, ensure_ascii=False)
    ok(f"Ohne ment1: Erwaehnungen als Text ({s})", s == 200 and "hk-mention" not in raw and "Anna" in raw and "fa-anna" not in raw, raw[:400])
    n0 = len(notes("b", be, "page.mention"))
    nid2 = api("a", an, "POST", "/api/notes", {"title": "Ohne"})[1]["id"]
    api("a", an, "PUT", f"/api/notes/{nid2}/shares", {"type": "remote", "target": f"@bert@{HB}", "role": "view"})
    api("a", an, "PATCH", f"/api/notes/{nid2}", doc([P("q1", "An " + M(f"@bert@{HB}", "Bert"))]))
    time.sleep(4)
    ok("Ohne ment1: keine Mitteilung an B", len(notes("b", be, "page.mention")) == n0, notes("b", be, "page.mention"))
    sql("a", "UPDATE fed_peer SET caps=?, caps_at=? WHERE id=?", (json.dumps(caps_a), time.time(), pa))

    # --- Synchronisierte Bloecke -------------------------------------------------------------------------------------
    na = api("a", an, "POST", "/api/notes", {"title": SECRET_T})[1]["id"]
    s, d = api("a", an, "PATCH", f"/api/notes/{na}", doc([{"id": "s1", "type": "synced", "data": {"blocks": [P("i1", SECRET)]}}]))
    sid = synced_of(json.loads(sql("a", "SELECT body FROM note WHERE id=?", (na,))[0][0])["blocks"])["data"]["sid"]
    ok(f"A: Original angelegt ({s})", s == 200 and len(sid) == 16, d)
    nk = api("a", an, "POST", "/api/notes", {"title": "Mit Kopie"})[1]["id"]
    api("a", an, "PATCH", f"/api/notes/{nk}", doc([P("k0", "Oben"), {"id": "k1", "type": "synced", "data": {"sid": sid}}]))
    api("a", an, "PUT", f"/api/notes/{nk}/shares", {"type": "remote", "target": f"@bert@{HB}", "role": "edit"})
    ok("B: zweite Freigabe angekommen", T.wait(lambda: len(sql("b", "SELECT 1 FROM fed_inbound")) >= 3, 20))
    rK, rA = ref(pa, nk), ref(pa, na)
    s, d = api("b", be, "GET", f"/api/notes/{rK}")
    k = synced_of(blocks_of(d))
    raw = json.dumps(d, ensure_ascii=False)
    ok(f"B ohne Zugriff auf das Original: noaccess ({s})", k and k["data"] == {"sid": sid, "src": {"noaccess": True}}, k)
    ok("B ohne Zugriff: weder Titel noch Inhalt noch Seiten-ID des Originals", SECRET not in raw and SECRET_T not in raw and str(rA) not in raw, raw[:400])
    api("a", an, "PUT", f"/api/notes/{na}/shares", {"type": "remote", "target": f"@bert@{HB}", "role": "edit"})
    time.sleep(1)
    s, d = api("b", be, "GET", f"/api/notes/{rK}")
    k = synced_of(blocks_of(d))
    src = (k or {}).get("data", {}).get("src", {})
    ok(f"B mit Zugriff: Inhalt + src (note negativ, role edit) ({s})", k and k["data"]["blocks"][0]["data"]["text"] == SECRET
       and src.get("note") == rA and src.get("role") == "edit" and isinstance(src.get("rev"), int), k)
    bl = blocks_of(d)
    for b in bl:
        if b.get("type") == "synced":
            b["data"]["blocks"].append(P("i2", "Ergaenzt von Bert"))
    s, d2 = api("b", be, "PATCH", f"/api/notes/{rK}", dict(doc(bl), rev=d.get("rev")))
    ok(f"B: Kopie bearbeitet ueber das Heim ({s})", s == 200 and (d2.get("synced") or {}).get(sid) == "ok", d2)
    ob = synced_of(json.loads(sql("a", "SELECT body FROM note WHERE id=?", (na,))[0][0])["blocks"])
    ok("A: Original enthaelt Berts Ergaenzung", [x["data"]["text"] for x in ob["data"]["blocks"]] == [SECRET, "Ergaenzt von Bert"], ob)
    kb = synced_of(json.loads(sql("a", "SELECT body FROM note WHERE id=?", (nk,))[0][0])["blocks"])
    ok("A: Kopie weiter nur sid", kb["data"] == {"sid": sid}, kb)

    # --- Gegenstelle ohne sync1 ---------------------------------------------------------------------------------------
    caps_a = set_caps(pa, ("sync1",))
    s, d = api("b", be, "GET", f"/api/notes/{rK}")
    k = synced_of(blocks_of(d))
    ok(f"Ohne sync1: nur sid (Rueckfall-Karte) ({s})", k and set(k["data"]) <= {"sid", "src"} and "blocks" not in k["data"] and SECRET not in json.dumps(d), k)
    bl = blocks_of(d)
    for b in bl:
        if b.get("type") == "synced":
            b["data"]["blocks"] = [P("x", "Ueberschrieben ohne sync1")]
            b["data"]["src"] = {"rev": 999}
    s, d2 = api("b", be, "PATCH", f"/api/notes/{rK}", dict(doc(bl), rev=d.get("rev")))
    ob = synced_of(json.loads(sql("a", "SELECT body FROM note WHERE id=?", (na,))[0][0])["blocks"])
    ok(f"Ohne sync1: Zurueckschreiben aendert das Original nicht ({s})", "Ueberschrieben" not in json.dumps(ob), ob)
    sql("a", "UPDATE fed_peer SET caps=?, caps_at=? WHERE id=?", (json.dumps(caps_a), time.time(), pa))


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
    print(f"\nFOEDERATION-ERWAEHNUNGEN {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    run()
