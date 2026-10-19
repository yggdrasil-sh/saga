"""Seitenbaum schlank und als Abgleich (2.2): GET /api/notes?delta=1 / ?since=<stand> / ?rows=0|only / ?fields=...

Geprueft wird gegen den ganzen Baum: Clients (je Konto und je Variante ganz/ohne Eintraege/nur Eintraege/wenige Felder)
holen nur Geaendertes und arbeiten es ein - nach jeder Runde Zufallsaenderungen (per API und direkt in der Datenbank:
neu, Titel/Symbol/Tags/Favorit/Vorlage/Sperre, verschoben, umsortiert, Archiv, Papierkorb, wiederhergestellt,
endgueltig geloescht, Freigaben an Person/Gruppe, Gruppe weg, Kommentare anderer, gelesen, erledigt) muss der Stand des
Clients GENAU dem ganzen Baum entsprechen (Inhalt und Reihenfolge). Dazu: Rechte (nie fremde Seiten, "removed" nur
eigene IDs), eine waehrend des Abrufs noch offene Transaktion geht nicht verloren, unpassende Stände -> full, und
GET /api/notes ohne Parameter ist byte-gleich zur bisherigen Umsetzung. Wegwerf-Konten ba-*, raeumt auf."""
import base64, http.client, json, os, random, time, urllib.parse
import psycopg
from itsdangerous import TimestampSigner
import auth
import db
from db import connect
from saga.notes import NOTE_META_COLS, _note_meta
from saga.pages import JSONResponse
from saga.sharing import shared_ids, unread_notes

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
A, BU, CU = "ba-a", "ba-b", "ba-c"
USERS = (A, BU, CU)
GRP = "ba-team"
VARIANTS = (("ganz", ""), ("ohne Eintraege", "&rows=0"), ("nur Eintraege", "&rows=only"),
            ("Felder", "&rows=0&fields=id,title,parent_id,archived,shared,unread"))
res, fails = [], []
n500 = [0]


def ok(n, c, detail=None):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, flush=True)
    if not c and detail is not None and len(fails) < 10:
        fails.append(n)
        print("     ", str(detail)[:1500], flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, u, body=None):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=60)
    h = {"Host": HOST, "Accept-Language": "de", "Cookie": "saga=" + ck(u), "X-Saga": "1"}
    if body is not None:
        h["Content-Type"] = "application/json"
    c.request(m, p, body=json.dumps(body) if body is not None else None, headers=h)
    r = c.getresponse()
    raw = r.read()
    if r.status >= 500:
        n500[0] += 1
    return r.status, raw


def jget(p, u, body=None, m="GET"):
    s, raw = req(m, p, u, body)
    return s, (json.loads(raw) if raw[:1] in (b"[", b"{") else raw), raw


# --- Referenz: GET /api/notes bisher (Stand vor 2.2b, unveraendert kopiert) ------------------------------------------
def old_tree_bytes(uid):
    with connect() as c:
        rows = c.execute(f"SELECT {NOTE_META_COLS}, archived, locked FROM note WHERE uid=? AND deleted_at IS NULL ORDER BY sort NULLS FIRST, created",
                         (uid,)).fetchall()
        sh = shared_ids(c, uid)
        un = unread_notes(c, uid, [uid])
    return JSONResponse([dict(_note_meta(r), archived=bool(r[9]), locked=bool(r[10]), shared=r[0] in sh, unread=r[0] in un) for r in rows]).body


class Client:
    """Wie ein App-/Web-Client: einmal ganz, danach nur Geaendertes einarbeiten."""

    def __init__(self, uid, name, var):
        self.uid, self.name, self.var = uid, name, var
        self.cur, self.items, self.fulls = None, {}, 0

    def sync(self):
        p = "/api/notes?" + (f"since={urllib.parse.quote(self.cur)}" if self.cur else "delta=1") + self.var
        s, d, _ = jget(p, self.uid)
        if s != 200 or not isinstance(d, dict):
            return s, d
        if d["full"]:
            self.fulls += 1
            self.list = list(d["notes"])
        else:
            # wie das Web (static/pages.js fetchTree): Entferntes und Geaendertes raus, Geaendertes hinter "after" wieder rein
            gone = set(d["removed"]) | {x["id"] for x in d["notes"]}
            chains = {}
            for x in d["notes"]:
                chains.setdefault(x["after"], []).append({k: v for k, v in x.items() if k != "after"})
            out = []

            def emit(k):
                for n in chains.pop(k, []):
                    out.append(n)
                    emit(n["id"])
            emit(None)
            for p in self.list:
                if p["id"] not in gone:
                    out.append(p)
                    emit(p["id"])
            if chains:                                # Vorgaenger unbekannt: darf nicht vorkommen
                self.lost = getattr(self, "lost", 0) + 1
                for lst in chains.values():
                    out.extend(lst)
            self.list = out
        self.items = {x["id"]: x for x in self.list}
        self.cur = d["cursor"]
        return s, d

    def ordered(self):
        return self.list


def owned_ever(uid):
    with connect() as c:
        return {r[0] for r in c.execute("SELECT id FROM note_tseq WHERE uid=?", (uid,))} | \
               {r[0] for r in c.execute("SELECT id FROM note WHERE uid=?", (uid,))}


def check_clients(clients, label):
    ok_all, bad = True, []
    own = {u: owned_ever(u) for u in USERS}
    for cl in clients:
        s, d = cl.sync()
        if s != 200 or not isinstance(d, dict):
            ok_all = False; bad.append((cl.uid, cl.name, s, str(d)[:200])); continue
        # Rechte: nur eigene Seiten, "removed" nur IDs eigener (frueherer) Seiten
        if any(x["id"] not in own[cl.uid] for x in d["notes"]) or any(i not in own[cl.uid] for i in d["removed"]):
            ok_all = False; bad.append((cl.uid, cl.name, "fremde ID")); continue
        s2, full, _ = jget("/api/notes?delta=1" + cl.var, cl.uid)
        ref = {x["id"]: x for x in full["notes"]}
        if cl.items != ref:
            diff = [(i, cl.items.get(i), ref.get(i)) for i in set(cl.items) | set(ref) if cl.items.get(i) != ref.get(i)]
            ok_all = False; bad.append((cl.uid, cl.name, len(diff), diff[:3])); continue
        if [x["id"] for x in cl.ordered()] != [x["id"] for x in full["notes"]] or getattr(cl, "lost", 0):
            ok_all = False; bad.append((cl.uid, cl.name, "Reihenfolge", getattr(cl, "lost", 0))); continue
        if not d["full"] and any("after" not in x for x in d["notes"]):
            ok_all = False; bad.append((cl.uid, cl.name, "after fehlt")); continue
        if "fields" in cl.var and any(set(x) - {"id", "title", "parent_id", "archived", "shared", "unread"} for x in full["notes"]):
            ok_all = False; bad.append((cl.uid, cl.name, "Felder")); continue
    ok(f"{label}: Abgleich = ganzer Baum (alle Konten, alle Varianten)", ok_all, bad)


def check_plain(label):
    """Ohne Parameter: byte-gleich zur bisherigen Umsetzung; ?delta=1 enthaelt dieselben Seiten in derselben Reihenfolge."""
    good, bad = True, []
    for u in USERS:
        s, raw = req("GET", "/api/notes", u)
        if s != 200 or raw != old_tree_bytes(u):
            good = False; bad.append((u, "bytes", s, raw[:200])); continue
        plain = json.loads(raw)
        _, d, _ = jget("/api/notes?delta=1", u)
        if plain != d["notes"]:
            good = False; bad.append((u, "delta=1 != Liste"))
        _, slim, _ = jget("/api/notes?rows=0", u)
        with connect() as c:
            dbs = {r[0] for r in c.execute("SELECT id FROM note WHERE uid=? AND kind='database'", (u,))}
        if slim != [x for x in plain if x["parent_id"] not in dbs]:
            good = False; bad.append((u, "rows=0"))
    ok(f"{label}: ohne Parameter byte-gleich, delta=1/rows=0 passend", good, bad)


# --- Daten und Aenderungen ------------------------------------------------------------------------------------------
def gen(r, uid, n):
    now = time.time()
    ids, dbs = [], []
    with connect() as c, c:
        for i in range(n):
            par = r.choice(ids) if ids and r.random() < 0.7 else None
            if dbs and r.random() < 0.3:
                par = r.choice(dbs)
            kind = "database" if r.random() < 0.08 and par not in dbs else "page"
            cur = c.execute("INSERT INTO note (uid, title, body, format, parent_id, sort, tags, kind, schema, created, updated, "
                            "deleted_at, archived, is_template, pinned, locked, icon) VALUES (?,?,?,'blocks',?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (uid, f"T{i} {r.randint(0, 999)}", '{"blocks": []}', par, r.choice([None, r.random() * 100]),
                             json.dumps(r.sample(["Arbeit", "privat", "Größe"], r.randint(0, 2)), ensure_ascii=False), kind,
                             json.dumps({"props": [], "views": []}) if kind == "database" else None,
                             now - 9000 + i + r.random() * 0.5, now - r.random() * 4000,
                             now - 10 if r.random() < 0.05 else None, now - 20 if r.random() < 0.06 else None,
                             1 if r.random() < 0.05 else 0, 1 if r.random() < 0.1 else 0, 1 if r.random() < 0.05 else 0,
                             r.choice(["", "📄", None])))
            ids.append(cur.lastrowid)
            if kind == "database":
                dbs.append(cur.lastrowid)
    return ids, dbs


def live_ids(uid, trash=False):
    with connect() as c:
        return [r[0] for r in c.execute("SELECT id FROM note WHERE uid=? AND deleted_at IS " + ("NOT NULL" if trash else "NULL")
                                        + " ORDER BY id", (uid,))]


def mutate_sql(r, uid, share_to):
    ids = live_ids(uid)
    now = time.time()
    with connect() as c, c:
        for nid in r.sample(ids, max(1, int(len(ids) * r.choice((0.01, 0.05, 0.15))))):
            k = r.random()
            if k < 0.12:
                c.execute("UPDATE note SET title=?, updated=? WHERE id=?", (f"neu {r.randint(0, 99999)}", now + r.random(), nid))
            elif k < 0.2:
                c.execute("UPDATE note SET body=?, updated=? WHERE id=?", ('{"blocks": []}', now + r.random(), nid))
            elif k < 0.25:
                c.execute("UPDATE note SET props=? WHERE id=?", (json.dumps({"x": r.random()}), nid))     # nicht im Baum
            elif k < 0.32:
                c.execute("UPDATE note SET icon=?, tags=? WHERE id=?", (r.choice(["", "⭐", "🗂"]), json.dumps([f"t{r.randint(0, 5)}"]), nid))
            elif k < 0.4:
                c.execute("UPDATE note SET pinned=1-pinned, is_template=? WHERE id=?", (r.choice((0, 0, 1)), nid))
            elif k < 0.45:
                c.execute("UPDATE note SET locked=1-locked WHERE id=?", (nid,))
            elif k < 0.55:
                c.execute("UPDATE note SET archived=? WHERE id=?", (None if r.random() < 0.5 else now, nid))
            elif k < 0.62:
                c.execute("UPDATE note SET sort=? WHERE id=?", (r.choice([None, r.random() * 100]), nid))
            elif k < 0.7:
                par = r.choice(ids + [None])
                if par != nid:
                    c.execute("UPDATE note SET parent_id=? WHERE id=?", (par, nid))
            elif k < 0.78:
                c.execute("UPDATE note SET deleted_at=? WHERE id=?", (now, nid))
            elif k < 0.82:
                c.execute("DELETE FROM note WHERE id=?", (nid,))
            elif k < 0.9:
                tt, tg = r.choice((("user", share_to), ("group", GRP)))
                c.execute("INSERT INTO note_share (note_id, owner_uid, target_type, target, role, created, created_by) VALUES (?,?,?,?,?,?,?) "
                          "ON CONFLICT DO NOTHING", (nid, uid, tt, tg, r.choice(["view", "comment", "edit"]), now, uid))
            elif k < 0.95:
                c.execute("DELETE FROM note_share WHERE note_id=? AND owner_uid=?", (nid, uid))
            else:
                c.execute("INSERT INTO note_comment (note_id, owner_uid, block_id, uid, body, created) VALUES (?,?,'',?,?,?)",
                          (nid, uid, share_to, "Kommentar", now))
        for nid in r.sample(live_ids(uid, True), min(3, len(live_ids(uid, True)))):      # Papierkorb: einige zurueck
            c.execute("UPDATE note SET deleted_at=NULL WHERE id=?", (nid,))
        for i in range(r.randint(0, 6)):                                                    # neue Seiten/Eintraege
            par = r.choice(ids + [None])
            c.execute("INSERT INTO note (uid, title, body, format, parent_id, sort, created, updated) VALUES (?,?,?,'blocks',?,?,?,?)",
                      (uid, f"neu {i}", '{"blocks": []}', par, now + i, now + i * 0.001, now))


def mutate_api(r, uid, other):
    """Dieselben Aenderungen ueber die echten Endpunkte (Trigger + Programmcode zusammen)."""
    ids = [i for i in live_ids(uid)]
    if not ids:
        return
    st = []
    s, d, _ = jget("/api/notes", uid, {"title": "API neu", "parent_id": r.choice(ids)}, m="POST"); st.append(s)
    new = d.get("id") if isinstance(d, dict) else None
    a = r.choice(ids)
    st.append(req("PATCH", f"/api/notes/{a}", uid, {"title": f"api {r.randint(0, 999)}", "icon": "🌿", "tags": ["x", "Y"]})[0])
    st.append(req("PATCH", f"/api/notes/{r.choice(ids)}", uid, {"pinned": r.random() < 0.5})[0])
    st.append(req("POST", f"/api/notes/{r.choice(ids)}/move", uid, {"parent_id": None, "index": r.randint(0, 5)})[0])
    st.append(req("POST", f"/api/notes/{r.choice(ids)}/archive", uid, {"archived": r.random() < 0.5})[0])
    t1 = r.choice(ids)
    st.append(req("DELETE", f"/api/notes/{t1}", uid)[0])
    tr = live_ids(uid, True)
    if tr:
        st.append(req("POST", f"/api/trash/{r.choice(tr)}/restore", uid)[0])
        st.append(req("DELETE", f"/api/trash/{r.choice(tr)}", uid)[0])
    sh = r.choice(live_ids(uid))
    st.append(req("PUT", f"/api/notes/{sh}/shares", uid, {"type": "user", "target": other, "role": "comment"})[0])
    s, cm, _ = jget(f"/api/notes/{sh}/comments", other, {"block_id": "", "body": "Frage von anderen"}, m="POST"); st.append(s)
    if r.random() < 0.5 and isinstance(cm, dict) and cm.get("id"):
        st.append(req("POST", f"/api/comments/{cm['id']}/seen", uid)[0])
    if r.random() < 0.3 and isinstance(cm, dict) and cm.get("id"):
        st.append(req("POST", f"/api/comments/{cm['id']}/resolve", uid, {"resolved": True})[0])
    if r.random() < 0.5:
        st.append(req("DELETE", f"/api/notes/{sh}/shares?type=user&target={other}", uid)[0])
    if new:
        st.append(req("PATCH", f"/api/notes/{new}", uid, {"title": "API umbenannt"})[0])
    return [s for s in st if s >= 500]


def cleanup():
    from saga.userdata import delete_user_data
    for u in USERS:
        if auth.get_user(u):
            delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM note WHERE uid LIKE 'ba-%'")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'ba-%' OR target LIKE 'ba-%'")
        c.execute("DELETE FROM note_comment WHERE owner_uid LIKE 'ba-%'")
        c.execute("DELETE FROM note_tseq WHERE uid LIKE 'ba-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'ba-%'")
        c.execute("DELETE FROM groups WHERE name LIKE 'ba-%'")


def epoch():
    with connect() as c:
        return c.execute("SELECT v FROM note_tseq_meta WHERE k='epoch'").fetchone()[0]


def main():
    from saga.i18n import set_lang
    set_lang("de")
    cleanup()
    prev = auth.settings(fresh=True)
    prev_en = prev.get("share_enabled", True)
    auth.save_setting("share_enabled", True)
    for u in USERS:
        auth.create_user(u, "Abgleich-Pass-1234", u.upper())
    with connect() as c, c:
        g = c.execute("INSERT INTO groups (name, description, created) VALUES (?, '', ?)", (GRP, time.time())).lastrowid
        c.execute("INSERT INTO user_group (user_id, group_id) VALUES (?, ?)", (auth.get_user(CU)["id"], g))
    time.sleep(5.6)                                   # Einstellungen sind im Server 5 s zwischengespeichert
    try:
        r = random.Random(4711)
        gen(r, A, 260); gen(r, BU, 120); gen(r, CU, 40)
        mutate_sql(r, A, BU); mutate_sql(r, BU, A)     # erste Freigaben/Kommentare
        check_plain("Start")
        clients = [Client(u, n, v) for u in USERS for n, v in VARIANTS]
        for cl in clients:
            cl.sync()
        ok("Erster Abruf: full mit Stand", all(cl.fulls == 1 and cl.cur for cl in clients))
        check_clients(clients, "Ohne Aenderung")
        s, d, _ = jget("/api/notes?since=" + urllib.parse.quote(clients[0].cur), A)
        s2, d2, _ = jget("/api/notes?since=" + urllib.parse.quote(d["cursor"]), A)
        ok("Ohne Aenderung: leerer Abgleich", s2 == 200 and not d2["full"] and d2["notes"] == [] and d2["removed"] == [], d2)

        for rnd in range(8):
            rr = random.Random(100 + rnd)
            mutate_sql(rr, A, BU)
            mutate_sql(rr, BU, A)
            if rnd % 2:
                mutate_sql(rr, CU, A)
            bad = mutate_api(rr, A, BU) + mutate_api(rr, BU, A)
            ok(f"Runde {rnd + 1}: API-Aenderungen ohne Serverfehler", not bad, bad)
            if rnd in (1, 2):                         # Datenbank mit Eintraegen endgueltig weg; Seite mit Kindern wird Datenbank und zurueck
                with connect() as c, c:
                    d = c.execute("SELECT p.id FROM note p WHERE p.uid=? AND p.kind='database' AND EXISTS "
                                  "(SELECT 1 FROM note k WHERE k.parent_id=p.id AND k.deleted_at IS NULL) LIMIT 1", (BU,)).fetchone()
                    if d:
                        c.execute("DELETE FROM note WHERE id=?", (d[0],))
                    p = c.execute("SELECT p.id FROM note p WHERE p.uid=? AND p.kind=? AND EXISTS "
                                  "(SELECT 1 FROM note k WHERE k.parent_id=p.id AND k.deleted_at IS NULL) ORDER BY p.id LIMIT 1",
                                  (A, "page" if rnd == 1 else "database")).fetchone()
                    if p:
                        c.execute("UPDATE note SET kind=? WHERE id=?", ("database" if rnd == 1 else "page", p[0]))
            if rnd in (4, 6):                         # viele auf einmal umsortiert (> 50: Vorgaenger per Fensterfunktion)
                with connect() as c, c:
                    for nid in rr.sample(live_ids(A), 80):
                        c.execute("UPDATE note SET sort=? WHERE id=?", (rr.choice([None, rr.random() * 100]), nid))
            if rnd == 3:                              # Gruppe: Mitglied raus, Freigaben an die Gruppe weg (Gruppe geloescht)
                with connect() as c, c:
                    c.execute("DELETE FROM user_group WHERE group_id=(SELECT id FROM groups WHERE name=?)", (GRP,))
                    c.execute("DELETE FROM note_share WHERE target_type='group' AND target=?", (GRP,))
            if rnd == 5:                              # Gruppe wieder mit Mitglied und Freigaben
                with connect() as c, c:
                    c.execute("INSERT INTO user_group (user_id, group_id) SELECT ?, id FROM groups WHERE name=?", (auth.get_user(CU)["id"], GRP))
            check_clients(clients, f"Runde {rnd + 1}")
        check_plain("Nach den Runden")
        ok("Abgleich statt ganzer Baum (keine unnoetigen full)", all(cl.fulls == 1 for cl in clients), [(c.uid, c.name, c.fulls) for c in clients])

        # Eine beim Abruf noch offene Transaktion: ihre Aenderung kommt beim naechsten Abgleich (nicht verloren)
        cl = Client(A, "offen", "")
        cl.sync()
        n1, n2 = live_ids(A)[:2]
        pc = psycopg.connect(db.DATABASE_URL)
        try:
            pc.execute("UPDATE note SET title='offene Transaktion' WHERE id=%s", (n1,))
            req("PATCH", f"/api/notes/{n2}", A, {"title": "dazwischen bestaetigt"})
            d = cl.sync()[1]
            ok("Offene Transaktion: noch nicht sichtbar, bestaetigte schon", cl.items.get(n1, {}).get("title") != "offene Transaktion"
               and cl.items.get(n2, {}).get("title") == "dazwischen bestaetigt", d)
            pc.commit()
        finally:
            pc.close()
        d = cl.sync()[1]
        ok("Offene Transaktion: nach Commit im naechsten Abgleich", cl.items[n1]["title"] == "offene Transaktion", d)
        check_clients([cl], "Nach offener Transaktion")

        # Unpassende Stände -> full (nie ein falscher Abgleich)
        good = clients[0].cur
        ep, x = good.split(".")
        cases = {"Unsinn": "abc", "leer": "", "andere Kennung": "000000000000." + x, "Zukunft": f"{ep}.{int(x) + 10 ** 9}",
                 "Null": f"{ep}.0", "zu lang": ep + "." + "9" * 25}
        for n, cur in cases.items():
            s, d, _ = jget("/api/notes?since=" + urllib.parse.quote(cur), A)
            ok(f"Stand {n} -> full", s == 200 and d["full"] is True and len(d["notes"]) == len(jget("/api/notes", A)[1]), (s, str(d)[:200]))
        with connect() as c, c:                       # Grabsteine aufgeraeumt (Grenze ueber dem Stand) -> full
            c.execute("INSERT INTO note_tseq_meta (k, v) VALUES ('horizon', ?) ON CONFLICT (k) DO UPDATE SET v=EXCLUDED.v", (x,))
        s, d, _ = jget("/api/notes?since=" + urllib.parse.quote(good), A)
        ok("Stand vor aufgeraeumten Grabsteinen -> full", s == 200 and d["full"] is True)
        with connect() as c, c:
            c.execute("DELETE FROM note_tseq_meta WHERE k='horizon'")
        old_ep = epoch()
        with psycopg.connect(db.DATABASE_URL) as pc:  # wie ein Neustart (z. B. nach Zurueckspielen eines Backups)
            db.init_tree_seq(pc)
            pc.commit()
        s, d, _ = jget("/api/notes?since=" + urllib.parse.quote(good), A)
        ok("Neue Kennung nach Neustart -> full", epoch() != old_ep and s == 200 and d["full"] is True and d["cursor"].startswith(epoch()))
        for cl in clients:
            cl.fulls = 0
        check_clients(clients, "Nach neuer Kennung")
        ok("Nach neuer Kennung: jeder Client genau einmal full", all(cl.fulls == 1 for cl in clients))

        # Grabsteine: endgueltig geloeschte Seite steht mit gone=1 da, Aufraeumen entfernt nur alte
        from saga import treesync
        with connect() as c, c:
            c.execute("UPDATE note_tseq SET at = at - ? WHERE uid=? AND gone=1", (treesync.TOMB_DAYS * 86400 + 60, A))
            n_old = c.execute("SELECT count(*) FROM note_tseq WHERE uid=? AND gone=1", (A,)).fetchone()[0]
        treesync._prune()                             # im Server hoechstens stuendlich - hier direkt
        with connect() as c:
            left = c.execute("SELECT count(*) FROM note_tseq WHERE uid=? AND gone=1", (A,)).fetchone()[0]
            hz = c.execute("SELECT v FROM note_tseq_meta WHERE k='horizon'").fetchone()
        ok("Alte Grabsteine aufgeraeumt, Grenze gesetzt", n_old > 0 and left == 0 and hz is not None, (n_old, left, hz))
        h = int(hz[0]) if hz else 0
        s, d, _ = jget("/api/notes?since=" + urllib.parse.quote(f"{epoch()}.{h}"), A)
        ok("Stand aus der Zeit vor dem Aufraeumen -> full", s == 200 and d["full"] is True)
        s, d, _ = jget("/api/notes?since=" + urllib.parse.quote(clients[0].cur), A)
        ok("Neuerer Stand bleibt nach dem Aufraeumen gueltig", s == 200 and d["full"] is False and int(clients[0].cur.split(".")[1]) > h)
        with connect() as c, c:
            c.execute("DELETE FROM note_tseq_meta WHERE k='horizon'")

        # Felder/Varianten, fremde Konten
        s, d, _ = jget("/api/notes?fields=id,title,unbekannt", A)
        ok("fields: nur id und title (unbekannte ignoriert)", s == 200 and isinstance(d, list) and d and all(set(x) == {"id", "title"} for x in d))
        s, d, _ = jget("/api/notes?rows=only&delta=1", BU)
        with connect() as c:
            own_b = {r[0] for r in c.execute("SELECT id FROM note WHERE uid=?", (BU,))}
        ok("rows=only: nur eigene Eintraege", s == 200 and all(x["id"] in own_b for x in d["notes"]))
        c0 = http.client.HTTPConnection(B_.hostname, B_.port, timeout=30)
        c0.request("GET", "/api/notes?delta=1", headers={"Host": HOST, "X-Saga": "1"})
        ok("Ohne Anmeldung: 401", c0.getresponse().status == 401)
        ok("Keine Serverfehler (HTTP 5xx) in dieser Reihe", n500[0] == 0, n500[0])
    finally:
        auth.save_setting("share_enabled", prev_en)
        cleanup()
    n_ok = sum(res)
    print(f"\n{n_ok}/{len(res)} " + ("ALLES GRUEN" if n_ok == len(res) else "FEHLER"))


if __name__ == "__main__":
    main()
