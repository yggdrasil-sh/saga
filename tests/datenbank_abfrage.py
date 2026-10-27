"""Datenbank-Ansichten serverseitig (2.2, GET .../rows mit view/filters/sorts/q/group/limit/offset):
1. Testvektoren (tests/dbquery_vectors.json, erzeugt mit tools/dbquery_vectors.mjs aus GENAU dem Code des Webs in
   static/pages.js): saga.dbquery muss ueber 400 Zufallsfaelle dasselbe liefern wie der Browser - Filter aller Arten,
   Suche (auch Relationen, ru/th, tuerkisches I, Emoji), bis zu 3 Sortierstufen, Gruppen (Tabelle/Liste/Board), leere
   und kaputte Werte, Fehlerfaelle (wo das Web mit TypeError abbricht), Zeitzonen und "heute".
2. HTTP gegen den Server: ohne neue Parameter unveraendert (Vergleich mit der bisherigen Umsetzung), Ansicht/Filter/
   Suche/Gruppen = Referenz ueber genau die Zeilen, die der Client bisher bekam; Blaettern lueckenlos, total/matched;
   Rechte (Eigentuemer, Freigabe nur Lesen, kein Zugriff), Papierkorb/Archiv/Vorlagen, Relationen-Titel nur wenn
   sichtbar, App-Schluessel (/api/app/rows), falsche Parameter -> 400.
Wegwerf-Konten dq-*, raeumt auf. Umgebung wie die anderen Tests (HK_TEST_BASE, BASE_URL, SESSION_SECRET)."""
import base64, hashlib, http.client, json, os, random, secrets, time, urllib.parse
from datetime import date
from zoneinfo import ZoneInfo
from itsdangerous import TimestampSigner
import auth
from db import connect
from saga import dbquery
from saga.databases import _first_image

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
A, BU, CU = "dq-a", "dq-b", "dq-c"
HERE = os.path.dirname(os.path.abspath(__file__))
res = []


def ok(n, c, detail=None):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, flush=True)
    if not c and detail is not None:
        print("     ", str(detail)[:1500], flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(p, u=None, bearer=None):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=60)
    h = {"Host": HOST, "Accept-Language": "de", "X-Saga": "1"}
    if u:
        h["Cookie"] = "saga=" + ck(u)
    if bearer:
        h["Authorization"] = "Bearer " + bearer
    c.request("GET", p, headers=h)
    r = c.getresponse()
    raw = r.read()
    return r.status, (json.loads(raw) if raw[:1] in (b"[", b"{") else raw), raw


def qs(**kw):
    return "?" + urllib.parse.urlencode({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v) for k, v in kw.items()})


# --- 1. Testvektoren aus dem Web-Code --------------------------------------------------------------------------------
def labels_of(pairs):
    m = {int(a): b for a, b in pairs}
    return lambda ids: {i: m[i] for i in ids if i in m}


def vectors():
    v = json.load(open(os.path.join(HERE, "dbquery_vectors.json"), encoding="utf-8"))
    bad, paged = [], 0
    for c in v["cases"]:
        d = v["dbs"][c["db"]]
        schema = dict(d["schema"], views=c["views"])
        args = {"view": c["view"], "tz": ZoneInfo(c["tz"]), "today": date.fromisoformat(c["today"])}
        if c["q"]:
            args["q"] = c["q"]
        exp = c["expected"]
        try:
            pk = {"people": d.get("people"), "me": d.get("me")}          # 2.5: Personen-Datenbanken (Namen, "ist mir zugewiesen")
            page, matched, groups = dbquery.run(schema, d["rows"], args, labels=labels_of(d["labels"]), untitled="Unbenannt", **pk)
            got = {"ids": [r["id"] for r in page], "matched": matched, "groups": groups}
            if groups is not None:          # Web zeigt leere Gruppen nicht (Tabelle/Liste) bzw. nur leeres "Ohne" nicht (Board)
                got["groups"] = [g for i, g in enumerate(groups) if g["count"] or (exp.get("board") and i > 0)]
            # Blaettern ueber denselben Fall: Seiten aneinandergehaengt = alles
            if got["ids"]:
                n = random.Random(c["k"]).randint(1, 7)
                parts = []
                for off in range(0, len(got["ids"]) + n, n):
                    pg, _, _ = dbquery.run(schema, d["rows"], dict(args, offset=off, limit=n), labels=labels_of(d["labels"]), **pk)
                    parts += [r["id"] for r in pg]
                paged += parts == got["ids"]
                if parts != got["ids"]:
                    bad.append((c["k"], "Blaettern", parts, got["ids"]))
        except dbquery.JsError as e:
            got = {"error": str(e)}
        if ("error" in exp) or ("error" in got):
            same = ("error" in exp) == ("error" in got)
        elif exp.get("engine"):         # widerspruechlicher Vergleich: Reihenfolge haengt von der JS-Engine ab
            same = sorted(got["ids"]) == sorted(exp["ids"]) and got["matched"] == exp["matched"] and got["groups"] == exp.get("groups")
        else:
            same = got["ids"] == exp["ids"] and got["matched"] == exp["matched"] and got["groups"] == exp.get("groups")
        if not same:
            bad.append((c["k"], exp, got))
    # Schnellweg der Sortierung (_sort_fast, ohne Vergleichsfunktion) = Vergleichsfunktion wie im Web, Fall fuer Fall
    fast_used, diff = 0, []
    orig = dbquery.Query._sort_fast
    for c in v["cases"]:
        d = v["dbs"][c["db"]]
        schema = dict(d["schema"], views=c["views"])
        args = {"view": c["view"], "tz": ZoneInfo(c["tz"]), "today": date.fromisoformat(c["today"]), "q": c["q"]}
        try:
            pk = {"people": d.get("people"), "me": d.get("me")}
            a = [r["id"] for r in dbquery.run(schema, d["rows"], args, labels=labels_of(d["labels"]), **pk)[0]]
            dbquery.Query._sort_fast = lambda self, rows, keys: None
            b = [r["id"] for r in dbquery.run(schema, d["rows"], args, labels=labels_of(d["labels"]), **pk)[0]]
        except dbquery.JsError:
            continue
        finally:
            dbquery.Query._sort_fast = orig
        q = dbquery.Query(schema, [], [s for s in (dbquery.view_of(schema, c["view"]) or {}).get("sorts") or [] if isinstance(s, dict)])
        try:
            fast_used += len(d["rows"]) > 2 and bool(q.sort_keys()) and orig(q, d["rows"], q.sort_keys()) is not None
        except dbquery.JsError:
            pass
        if a != b and not c["expected"].get("engine"):
            diff.append((c["k"], a, b))
    ok(f"Sortierung: Schnellweg = Vergleichsfunktion (Schnellweg in {fast_used} Faellen)", not diff and fast_used >= 30, diff[:3])
    n = len(v["cases"])
    ok(f"Testvektoren: {n} Faelle aus dem Web-Code (static/pages.js) = saga.dbquery", not bad, bad[:3])
    ok(f"Testvektoren: Blaettern lueckenlos ({paged} Faelle)", paged > 100 and not [b for b in bad if b[1] == "Blaettern"])
    ok("Testvektoren decken Fehler, Gruppen und Suche ab",
       sum(1 for c in v["cases"] if "error" in c["expected"]) >= 10 and sum(1 for c in v["cases"] if c["expected"].get("groups")) >= 30
       and sum(1 for c in v["cases"] if c["q"]) >= 100)
    return v


# --- 2. HTTP ---------------------------------------------------------------------------------------------------------
def old_rows(owner, nid, shared):
    """/rows wie vor 2.2 (Referenz, unveraendert kopiert aus schnell_server.old_rows - ohne Freigabe-Baum)."""
    with connect() as c:
        db = c.execute("SELECT schema FROM note WHERE id=? AND uid=? AND kind='database' AND deleted_at IS NULL", (nid, owner)).fetchone()
        rows = c.execute("SELECT id, title, icon, props, updated, created, cover, body, format FROM note WHERE uid=? AND parent_id=? "
                         "AND deleted_at IS NULL AND is_template=0" + (" AND archived IS NULL" if shared else "")
                         + " ORDER BY sort NULLS FIRST, created", (owner, nid)).fetchall()
        tpls = [] if shared else c.execute("SELECT id, title, icon FROM note WHERE uid=? AND parent_id=? AND deleted_at IS NULL AND is_template=1 "
                                           "ORDER BY sort NULLS FIRST, created", (owner, nid)).fetchall()
    return {"schema": json.loads(db[0]) if db[0] else {"props": []}, "templates": [{"id": t[0], "title": t[1] or "", "icon": t[2] or ""} for t in tpls],
            "rows": [{"id": r[0], "title": r[1] or "", "icon": r[2] or "", "props": json.loads(r[3]) if r[3] else {},
                      "updated": r[4], "created": r[5], "preview": r[6] or _first_image(r[7], r[8])} for r in rows]}


def insert_note(c, uid, title, parent=None, kind="page", schema=None, props=None, created=None, archived=None, deleted=None, tpl=0, body=None):
    now = time.time()
    cur = c.execute("INSERT INTO note (uid, title, body, format, parent_id, kind, schema, props, created, updated, archived, deleted_at, is_template) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (uid, title, body or json.dumps({"blocks": []}), "blocks", parent, kind, json.dumps(schema, ensure_ascii=False) if schema else None,
                     json.dumps(props, ensure_ascii=False) if props is not None else None, created or now, (created or now) + 60, archived, deleted, tpl))
    return cur.lastrowid


def build(v, k):
    """Datenbank aus Vektor-DB k fuer A: Ziel-Datenbank fuer die Relation, Zeilen (rohe Werte, auch kaputte),
    dazu archivierte, geloeschte und Vorlagen-Zeilen. -> (db_id, ziel_id, faelle)"""
    d = v["dbs"][k]
    cases = [c for c in v["cases"] if c["db"] == k]
    with connect() as c, c:
        tgt = insert_note(c, A, f"Ziel {k}", kind="database", schema={"props": [], "views": []})
        idmap = {}
        for vid, title in d["labels"]:
            idmap[int(vid)] = insert_note(c, A, title, parent=tgt, props={})
        schema = json.loads(json.dumps(d["schema"]))
        for p in schema["props"]:
            if p["type"] == "relation":
                p["target"] = tgt
        schema["views"] = [cs["views"][0] for cs in cases]
        db = insert_note(c, A, f"Abfrage {k} – Проверка ทดสอบ", kind="database", schema=schema)
        for i, r in enumerate(d["rows"]):
            props = {pid: ([idmap.get(x, x) if isinstance(x, int) else x for x in val] if pid == "r1" and isinstance(val, list) else val)
                     for pid, val in r["props"].items()}
            insert_note(c, A, r["title"], parent=db, props=props, created=r["created"] + i * 0.001)
        insert_note(c, A, "Alpha archiviert", parent=db, props={"t1": "alpha"}, archived=time.time() - 50)
        insert_note(c, A, "Alpha geloescht", parent=db, props={"t1": "alpha"}, deleted=time.time() - 50)
        insert_note(c, A, "Alpha Vorlage", parent=db, props={"t1": "alpha"}, tpl=1)
        bild = json.dumps({"blocks": [{"type": "image", "data": {"file": {"url": "/files/" + "ab" * 12}}}]})
        insert_note(c, A, "Mit Bild", parent=db, props={"t1": "bild"}, body=bild)
    return db, tgt, cases


def labels_for(viewer, owner, tgt):
    """Was der Browser des Betrachters ueber byId kennt: eigene aktive Seiten bzw. den Freigabe-Baum."""
    with connect() as c:
        rows = c.execute("SELECT id, title, archived FROM note WHERE uid=? AND parent_id=? AND deleted_at IS NULL", (owner, tgt)).fetchall()
        shared = {r[0] for r in c.execute("SELECT note_id FROM note_share WHERE owner_uid=? AND target=?", (owner, viewer))}
    if viewer == owner:
        m = {r[0]: r[1] or "" for r in rows if not r[2]}
    else:
        m = {r[0]: r[1] or "" for r in rows if not r[2] and tgt in shared}
    return lambda ids: {i: m[i] for i in ids if i in m}


def expect(full, args, labels):
    try:
        page, matched, groups = dbquery.run(full["schema"], full["rows"], args, labels=labels, untitled="Unbenannt")
    except dbquery.JsError:
        return None
    return [r["id"] for r in page], matched, groups


def args_of(case):
    a = {"view": case["view"], "tz": case["tz"], "today": case["today"]}
    if case["q"]:
        a["q"] = case["q"]
    return a


def parsed(a):
    out = dict(a)
    out["tz"], out["today"] = ZoneInfo(a["tz"]), date.fromisoformat(a["today"])
    return out


def check_views(db, tgt, cases, viewer, label):
    """Jede Ansicht der Datenbank per HTTP = Referenz ueber genau die Zeilen, die der Betrachter ohne Parameter bekommt."""
    s, full, _ = req(f"/api/notes/{db}/rows", viewer)
    lab = labels_for(viewer, A, tgt)
    bad, n400, nonempty = [], 0, 0
    for cs in cases:
        a = args_of(cs)
        exp = expect(full, parsed(a), lab)
        s2, d, _ = req(f"/api/notes/{db}/rows" + qs(**a), viewer)
        if exp is None:
            n400 += s2 == 400
            if s2 != 400:
                bad.append((cs["k"], "400 erwartet", s2))
            continue
        ids = [r["id"] for r in d.get("rows", [])] if s2 == 200 else None
        grp = d.get("groups") if s2 == 200 else None
        if s2 != 200 or ids != exp[0] or d.get("matched") != exp[1] or grp != exp[2] or d.get("total") != len(full["rows"]):
            bad.append((cs["k"], s2, ids, exp))
            continue
        nonempty += bool(ids)
        # Zeilen der Antwort = dieselben Objekte wie ohne Parameter (Werte, Vorschaubild ...)
        byid = {r["id"]: r for r in full["rows"]}
        if any(r != byid[r["id"]] for r in d["rows"]):
            bad.append((cs["k"], "Zeileninhalt"))
        # seitenweise (7er) lueckenlos
        parts, off = [], 0
        while True:
            s3, pg, _ = req(f"/api/notes/{db}/rows" + qs(**a, limit=7, offset=off), viewer)
            if s3 != 200 or not pg["rows"]:
                break
            parts += [r["id"] for r in pg["rows"]]
            off += 7
        if parts != exp[0]:
            bad.append((cs["k"], "Blaettern", parts, exp[0]))
    ok(f"{label}: {len(cases)} Ansichten per HTTP = Referenz (davon {nonempty} mit Treffern, {n400}x 400 wie Web-Fehler), auch seitenweise",
       s == 200 and not bad, bad[:3])
    return full


def main():
    from saga.i18n import set_lang
    set_lang("de")
    cleanup()
    prev_en = auth.settings(fresh=True).get("share_enabled", True)
    auth.save_setting("share_enabled", True)
    for u in (A, BU, CU):
        auth.create_user(u, "Abfrage-Pass-1234", u.upper())
    time.sleep(5.6)                                   # Einstellungen sind im Server 5 s zwischengespeichert
    try:
        v = vectors()
        tok = "hk_" + secrets.token_urlsafe(24)
        with connect() as c, c:
            c.execute("INSERT INTO app_token (uid, name, hash, prefix, created) VALUES (?,?,?,?,?)",
                      (A, "dq", hashlib.sha256(tok.encode()).hexdigest(), tok[:8], time.time()))
        for k in (0, 3, 7, 12, 21, 33):
            db, tgt, cases = build(v, k)
            # ohne neue Parameter: wie bisher (Inhalt) und unabhaengig von fremden Parametern (Bytes)
            s, d, raw = req(f"/api/notes/{db}/rows", A)
            ref = dict(old_rows(A, db, False), role="owner")
            _, _, raw2 = req(f"/api/notes/{db}/rows?_=1&x=y", A)
            ok(f"DB {k}: ohne Parameter = bisherige Antwort (Inhalt, Reihenfolge, keine neuen Felder)", s == 200 and d == ref and raw == raw2,
               (sorted(d), sorted(ref)) if isinstance(d, dict) else d)
            ok(f"DB {k}: Archiv beim Eigentuemer dabei, Papierkorb/Vorlage nicht", "Alpha archiviert" in [r["title"] for r in d["rows"]]
               and "Alpha geloescht" not in [r["title"] for r in d["rows"]] and "Alpha Vorlage" not in [r["title"] for r in d["rows"]]
               and [t["title"] for t in d["templates"]] == ["Alpha Vorlage"])
            check_views(db, tgt, cases, A, f"DB {k} Eigentuemer")
            # Freigabe nur Lesen an B: ohne Archiv/Vorlagen; Relationen-Titel nur, wenn auch die Ziel-Datenbank sichtbar ist
            with connect() as c, c:
                c.execute("INSERT INTO note_share (note_id, owner_uid, target_type, target, role, created, created_by) VALUES (?,?,?,?,?,?,?)",
                          (db, A, "user", BU, "view", time.time(), A))
            fb = check_views(db, tgt, cases, BU, f"DB {k} Freigabe (Ziel nicht geteilt)")
            ok(f"DB {k}: Freigabe ohne Archiv/Vorlagen, Rolle view", fb.get("role") == "view" and fb["templates"] == []
               and "Alpha archiviert" not in [r["title"] for r in fb["rows"]])
            s, qb, _ = req(f"/api/notes/{db}/rows" + qs(q="kunde müller", view=cases[0]["view"]), BU)
            s2, qa, _ = req(f"/api/notes/{db}/rows" + qs(q="kunde müller", view=cases[0]["view"]), A)
            with connect() as c, c:
                c.execute("INSERT INTO note_share (note_id, owner_uid, target_type, target, role, created, created_by) VALUES (?,?,?,?,?,?,?)",
                          (tgt, A, "user", BU, "view", time.time(), A))
            check_views(db, tgt, cases, BU, f"DB {k} Freigabe (Ziel geteilt)")
            s3, qb2, _ = req(f"/api/notes/{db}/rows" + qs(q="kunde müller", view=cases[0]["view"]), BU)
            if s == s2 == s3 == 200 and qa["matched"]:
                ok(f"DB {k}: Suche nach Relationen-Titel nur mit Zugriff aufs Ziel", qb["matched"] <= qa["matched"] and qb2["matched"] >= qb["matched"])
            s, _, _ = req(f"/api/notes/{db}/rows" + qs(view=cases[0]["view"], limit=5), CU)
            ok(f"DB {k}: ohne Zugriff auch mit Parametern 404", s == 404)
            # App-Schluessel (Widget): dieselbe Logik
            sa, wa, _ = req(f"/api/app/rows/{db}" + qs(view=cases[1]["view"], tz=cases[1]["tz"], today=cases[1]["today"], limit=5), bearer=tok)
            sc, wc, _ = req(f"/api/notes/{db}/rows" + qs(view=cases[1]["view"], tz=cases[1]["tz"], today=cases[1]["today"], limit=5), A)
            ok(f"DB {k}: /api/app/rows mit Ansicht = Web-Endpunkt", (sa, sc) in ((200, 200), (400, 400))
               and (sa == 400 or ([r["id"] for r in wa["rows"]] == [r["id"] for r in wc["rows"]] and wa["matched"] == wc["matched"])))
            sw, ww, raww = req(f"/api/app/rows/{db}", bearer=tok)
            ok(f"DB {k}: /api/app/rows ohne Parameter wie bisher", sw == 200 and ww == old_rows(A, db, False))
        rest(db, tgt, A)
    finally:
        auth.save_setting("share_enabled", prev_en)
        cleanup()
    n_ok = sum(res)
    print(f"\n{n_ok}/{len(res)} " + ("ALLES GRUEN" if n_ok == len(res) else "FEHLER"))


def rest(db, tgt, u):
    """Nur Blaettern (SQL), explizite filters/sorts/group, Grenzen und falsche Parameter."""
    s, full, _ = req(f"/api/notes/{db}/rows", u)
    ids = [r["id"] for r in full["rows"]]
    parts, off = [], 0
    while True:
        s2, pg, _ = req(f"/api/notes/{db}/rows" + qs(limit=9, offset=off), u)
        if s2 != 200 or not pg["rows"]:
            break
        parts += [r["id"] for r in pg["rows"]]
        ok_meta = pg["total"] == len(ids) and pg["matched"] == len(ids) and pg["limit"] == 9 and pg["offset"] == off
        off += 9
    ok("Nur Blaettern (LIMIT/OFFSET in SQL): lueckenlos in der bisherigen Reihenfolge, total/matched/limit/offset", parts == ids and ok_meta)
    byid = {r["id"]: r for r in full["rows"]}
    ok("Nur Blaettern: Zeilen gleich wie ohne Parameter", all(r == byid[r["id"]] for r in pg.get("rows", [])) and
       all(r == byid[r["id"]] for r in req(f"/api/notes/{db}/rows" + qs(limit=50), u)[1]["rows"]))
    s, d, _ = req(f"/api/notes/{db}/rows" + qs(offset=len(ids) + 5), u)
    ok("offset hinter dem Ende -> leere Seite, total stimmt", s == 200 and d["rows"] == [] and d["total"] == len(ids))
    s, d, _ = req(f"/api/notes/{db}/rows" + qs(limit=10 ** 7), u)
    ok("limit wird auf 5000 begrenzt", s == 200 and d["limit"] == 5000 and len(d["rows"]) == len(ids))
    # explizite Filter/Sortierung/Gruppe (eingebettete Ansichten haben eigene, nicht in der Datenbank gespeicherte)
    tz = {"tz": "Europe/Berlin", "today": "2026-10-19"}
    for fl, so, grp, q in (([{"pid": "t1", "op": "not_empty"}], [{"pid": "title", "dir": "desc"}], None, None),
                           ([], [{"pid": "n1", "dir": "asc"}, {"pid": "title", "dir": "desc"}], "s1", None),
                           ([{"pid": "c1", "op": "checked"}], [], "", "a"),
                           ([{"pid": "d1", "op": "this_month"}], [{"pid": "cr", "dir": "desc"}], "s2", "ПРИВЕТ")):
        a = dict(tz, filters=fl, sorts=so)
        if grp is not None:
            a["group"] = grp
        if q:
            a["q"] = q
        pa = dict(a, tz=ZoneInfo(a["tz"]), today=date.fromisoformat(a["today"]))
        exp = expect(full, pa, labels_for(u, A, tgt))
        s, d, _ = req(f"/api/notes/{db}/rows" + qs(**a), u)
        if exp is None:                     # Web bricht hier ab (z. B. Suche ueber kaputte Relationswerte) -> 400
            ok(f"filters/sorts/group explizit {json.dumps(fl, ensure_ascii=False)[:40]} {grp!r} q={q!r}: wie Web-Fehler 400", s == 400, d)
            continue
        ok(f"filters/sorts/group explizit {json.dumps(fl, ensure_ascii=False)[:40]} {grp!r} q={q!r} = Referenz",
           s == 200 and [r["id"] for r in d["rows"]] == exp[0] and d["matched"] == exp[1] and d.get("groups") == exp[2], (d, exp))
    for bad in ("limit=0", "limit=abc", "offset=-1", "filters=nicht-json", "filters=%7B%7D", "sorts=5", "tz=Mars%2FBasis", "today=2026-13-01",
                "q=" + "x" * 600, "offset=99999999999999999999"):
        s, d, _ = req(f"/api/notes/{db}/rows?{bad}", u)
        ok(f"falscher Parameter {bad[:20]} -> 400", s == 400)
    s, d, _ = req(f"/api/notes/{db}/rows" + qs(filters=[{"pid": "r1", "op": "contains", "value": "1"}]), u)
    ok("Relation 'enthaelt' bei Zeilen ohne Relation: kein Abbruch mehr (Zeilen ohne Verknuepfung zaehlen als leere Liste)",
       s == 200 and isinstance(d.get("rows"), list), (s, d))
    s2, d2, _ = req(f"/api/notes/{db}/rows" + qs(filters=[{"pid": "r1", "op": "not_contains", "value": "1"}]), u)
    ok("Relation 'enthaelt nicht' bei Zeilen ohne Relation: Zeilen ohne Verknuepfung gehoeren dazu",
       s2 == 200 and s == 200 and d2.get("matched", 0) + d.get("matched", 0) >= len(d2.get("rows", [])), (s2, d2))


def cleanup():
    from saga.userdata import delete_user_data
    for u in (A, BU, CU):
        if auth.get_user(u):
            delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM note WHERE uid LIKE 'dq-%'")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'dq-%' OR target LIKE 'dq-%'")
        c.execute("DELETE FROM app_token WHERE uid LIKE 'dq-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'dq-%'")


if __name__ == "__main__":
    main()
