"""Freigabe-Baum mit Zwischenspeicher (2.2, sharing.Access.tree): nach JEDER Art von Aenderung liefert der Baum genau
das, was eine frische Abfrage ohne Zwischenspeicher liefert und was der alte Algorithmus (vor 2.2, als Referenz kopiert)
geliefert hat - im Testprozess (eigener Zwischenspeicher) und per HTTP (/api/shared, Zwischenspeicher im Server).

Zufallsaenderungen: Titel, Symbol, Schlagworte, angeheftet, updated, sort/created (Reihenfolge), Verschieben (auch in
und aus geteilten Aesten, nach oben), Papierkorb/zurueck, Archiv/zurueck, Vorlage, Art (Datenbank), endgueltig loeschen,
neue Seiten, Seite an ein anderes Konto, Freigaben an Person/Gruppe neu/weg/andere Rolle, Gruppenmitgliedschaft lokal und
aus LDAP/OIDC, nur Inhalt (aendert den Baum nicht: Treffer im Zwischenspeicher). Dazu die Faelle, in denen ein
Zwischenspeicher falsch werden koennte: Aenderung einer Transaktion, die beim Fuellen schon lief (aeltere Nummer);
noch nicht bestaetigte Aenderung (anderer Verbindung und eigener Transaktion, danach Rollback); REPEATABLE READ (alter
Stand bleibt sichtbar wie ohne Zwischenspeicher); Begrenzung der Zeilen. Wegwerf-Konten bc-*, raeumt auf."""
import base64, http.client, json, os, random, time, urllib.parse
from itsdangerous import TimestampSigner
import auth
from db import connect
from saga import sharing
from saga.notes import NOTE_META_COLS
from saga.sharing import Access, _best

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
A, BU, CU, DU = "bc-a", "bc-b", "bc-c", "bc-d"
GRP, EXT = "bc-team", "bc-extern"
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


def jget(p, u):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=60)
    c.request("GET", p, headers={"Host": HOST, "Accept-Language": "de", "Cookie": "saga=" + ck(u), "X-Saga": "1"})
    r = c.getresponse()
    raw = r.read()
    if r.status >= 500:
        n500[0] += 1
    return r.status, raw


# --- Referenzen -----------------------------------------------------------------------------------------------------
class OldAccess(Access):
    """Freigabe-Baum wie vor 2.2 (ganzer Baum des Eigentuemers, Vererbung in Python) - unveraendert kopiert."""
    def tree(self, owner, meta=False):
        if owner == self.uid or not self.on():
            return {}
        sh = self.shares_from(owner)
        if not sh:
            self._tree[owner] = {}
            return {}
        cols = f", {NOTE_META_COLS}" if meta else ""
        rows = self.c.execute(f"SELECT n.id, n.parent_id, (n.deleted_at IS NOT NULL OR n.archived IS NOT NULL OR n.is_template<>0){cols} "
                              "FROM note n WHERE n.uid=? ORDER BY n.sort NULLS FIRST, n.created", (owner,)).fetchall()
        par = {r[0]: r[1] for r in rows}
        bad = {r[0]: bool(r[2]) for r in rows}
        memo = {}
        for n in par:
            stack, x = [], n
            while x is not None and x in par and x not in memo and len(stack) < 80:
                stack.append(x)
                x = par[x]
            up = memo.get(x)
            for y in reversed(stack):
                up = None if bad[y] else _best(sh.get(y), up)
                memo[y] = up
        roles = {k: v for k, v in memo.items() if v}
        self._tree[owner] = roles
        if meta:
            return {r[0]: (roles[r[0]], r[3:]) for r in rows if r[0] in roles}
        return roles


class NoCache:
    """Zwischenspeicher im Testprozess abschalten (Referenz: dieselbe Abfrage, immer frisch)."""
    def __enter__(self):
        self.orig = sharing._tree_cached
        sharing._tree_cached = lambda c, key: (None, None)

    def __exit__(self, *a):
        sharing._tree_cached = self.orig


def trees(u, cls=Access):
    with connect() as c:
        return list(cls(c, u).tree(A).items()), list(cls(c, u).tree(A, meta=True).items())


def compare(label, http=True):
    bad = []
    for u in (BU, CU, DU):
        got = trees(u)
        with NoCache():
            ref = trees(u)
        old = trees(u, OldAccess)
        if got != ref:
            bad.append((u, "frisch", label))
        # alte Umsetzung: gleiche Seiten/Rollen; Reihenfolge bei gleichem sort/created ohne id (dort nicht festgelegt)
        if sorted(got[0]) != sorted(old[0]) or sorted(got[1]) != sorted(old[1]):
            bad.append((u, "alt", label, set(got[0]) ^ set(old[0])))
        if http:
            s, raw = jget("/api/shared", u)
            with NoCache():
                exp = sharing.shared_list(u)
            if s != 200 or json.loads(raw) != exp:
                bad.append((u, "http", s, label))
    return bad


# --- Zufallsdaten und Aenderungen -------------------------------------------------------------------------------------
TITLES = "Projekt Größe Straße Проект встреча โครงการ การประชุม İstanbul ΟΔΟΣ café niño 100% a_b".split()


def ins(c, r, par, kind="page"):
    now = time.time()
    return c.execute("INSERT INTO note (uid, title, body, format, parent_id, sort, tags, kind, schema, created, updated, "
                     "deleted_at, archived, is_template, icon, pinned) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (A, " ".join(r.sample(TITLES, r.randint(0, 3))), '{"blocks":[]}', "blocks", par,
                      r.choice([None, None, r.randint(0, 5), r.random() * 10]), json.dumps(r.sample(TITLES, r.randint(0, 2)), ensure_ascii=False),
                      kind, json.dumps({"props": [], "views": []}) if kind == "database" else None,
                      r.choice([now - 100, now - 100, now - r.random() * 1000]), now - r.random() * 500,
                      now if r.random() < 0.04 else None, now if r.random() < 0.05 else None, 1 if r.random() < 0.04 else 0,
                      r.choice(["", "", "📁", "★"]), 1 if r.random() < 0.1 else 0)).lastrowid


def gen(r, n):
    ids = []
    with connect() as c, c:
        for _ in range(n):
            par = r.choice(ids) if ids and r.random() < 0.85 else None
            ids.append(ins(c, r, par, "database" if r.random() < 0.05 else "page"))
        for nid in r.sample(ids, 6):
            share(c, r, nid)
    return ids


def share(c, r, nid):
    tt, tg = r.choice([("user", BU), ("user", BU), ("group", GRP), ("group", EXT), ("user", DU)])
    c.execute("INSERT INTO note_share (note_id, owner_uid, target_type, target, role, created, created_by) VALUES (?,?,?,?,?,?,?) "
              "ON CONFLICT DO NOTHING", (nid, A, tt, tg, r.choice(["view", "comment", "edit"]), time.time(), A))


def live_ids(c):
    return [x[0] for x in c.execute("SELECT id FROM note WHERE uid=?", (A,)).fetchall()]


def mutate(r):
    """Eine zufaellige Aenderung (eigene Transaktion) -> Name der Aenderung."""
    now = time.time()
    with connect() as c, c:
        ids = live_ids(c)
        nid = r.choice(ids)
        ops = ["titel", "symbol", "tags", "pin", "updated", "sort", "created", "verschieben", "nach_oben", "papierkorb",
               "zurueck", "archiv", "vorlage", "art", "loeschen", "neu", "konto", "freigabe", "freigabe_weg", "rolle",
               "gruppe", "extern", "inhalt"]
        op = r.choice(ops)
        if op == "titel":
            c.execute("UPDATE note SET title=? WHERE id=?", (" ".join(r.sample(TITLES, 2)), nid))
        elif op == "symbol":
            c.execute("UPDATE note SET icon=? WHERE id=?", (r.choice(["", "🚀", "x"]), nid))
        elif op == "tags":
            c.execute("UPDATE note SET tags=? WHERE id=?", (json.dumps(r.sample(TITLES, 2), ensure_ascii=False), nid))
        elif op == "pin":
            c.execute("UPDATE note SET pinned=1-pinned WHERE id=?", (nid,))
        elif op == "updated":
            c.execute("UPDATE note SET updated=? WHERE id=?", (now + r.random(), nid))
        elif op == "sort":
            c.execute("UPDATE note SET sort=? WHERE id=?", (r.choice([None, r.randint(0, 5), r.random() * 10]), nid))
        elif op == "created":
            c.execute("UPDATE note SET created=? WHERE id=?", (now - r.random() * 2000, nid))
        elif op == "verschieben":
            # Eltern immer mit kleinerer id (wie beim Anlegen): keine Zyklen
            c.execute("UPDATE note SET parent_id=? WHERE id=?", (r.choice([i for i in ids if i < nid] or [None]), nid))
        elif op == "nach_oben":
            c.execute("UPDATE note SET parent_id=NULL WHERE id=?", (nid,))
        elif op == "papierkorb":
            c.execute("UPDATE note SET deleted_at=? WHERE id=?", (now, nid))
        elif op == "zurueck":
            c.execute("UPDATE note SET deleted_at=NULL, archived=NULL WHERE id=?", (nid,))
        elif op == "archiv":
            c.execute("UPDATE note SET archived=? WHERE id=?", (now, nid))
        elif op == "vorlage":
            c.execute("UPDATE note SET is_template=1-is_template WHERE id=?", (nid,))
        elif op == "art":
            c.execute("UPDATE note SET kind=CASE WHEN kind='database' THEN 'page' ELSE 'database' END WHERE id=?", (nid,))
        elif op == "loeschen":
            c.execute("DELETE FROM note_share WHERE note_id=?", (nid,))
            c.execute("UPDATE note SET parent_id=NULL WHERE parent_id=?", (nid,))
            c.execute("DELETE FROM note WHERE id=?", (nid,))
        elif op == "neu":
            ins(c, r, r.choice(ids + [None]))
        elif op == "konto":
            c.execute("UPDATE note SET uid=? WHERE id=?", (DU, nid))                    # Seite gehoert jetzt einem anderen
        elif op == "freigabe":
            share(c, r, nid)
        elif op == "freigabe_weg":
            c.execute("DELETE FROM note_share WHERE id IN (SELECT id FROM note_share WHERE owner_uid=? ORDER BY random() LIMIT 1)", (A,))
        elif op == "rolle":
            c.execute("UPDATE note_share SET role=? WHERE id IN (SELECT id FROM note_share WHERE owner_uid=? ORDER BY random() LIMIT 1)",
                      (r.choice(["view", "comment", "edit"]), A))
        elif op == "gruppe":
            g = c.execute("SELECT id FROM groups WHERE name=?", (GRP,)).fetchone()[0]
            uid = auth.get_user(r.choice([BU, CU, DU]))["id"]
            if not c.execute("DELETE FROM user_group WHERE user_id=? AND group_id=?", (uid, g)).rowcount:
                c.execute("INSERT INTO user_group (user_id, group_id) VALUES (?, ?)", (uid, g))
        elif op == "extern":
            uid = auth.get_user(r.choice([BU, CU, DU]))["id"]
            if not c.execute("DELETE FROM user_ext_group WHERE user_id=? AND name=?", (uid, EXT)).rowcount:
                c.execute("INSERT INTO user_ext_group (user_id, name) VALUES (?, ?)", (uid, EXT))
        else:                                                   # nur Inhalt: Baum unveraendert
            c.execute("UPDATE note SET body=? WHERE id=?", (json.dumps({"blocks": [{"type": "paragraph", "data": {"text": str(now)}}]}), nid))
    return op


def title_of(u, nid):
    m = dict(trees(u)[1])
    return m[nid][1][1] if nid in m else None


def visible_nid(u):
    m = trees(u)[1]
    return m[len(m) // 2][0] if m else None


def cleanup():
    from saga.userdata import delete_user_data
    for u in (A, BU, CU, DU):
        if auth.get_user(u):
            delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'bc-%' OR target LIKE 'bc-%'")
        c.execute("UPDATE note SET parent_id=NULL WHERE uid LIKE 'bc-%'")
        c.execute("DELETE FROM note WHERE uid LIKE 'bc-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'bc-%'")
        c.execute("DELETE FROM groups WHERE name LIKE 'bc-%'")


def special():
    """Faelle rund um Transaktionen, die ein Zwischenspeicher falsch machen koennte."""
    nid = visible_nid(BU)
    ok("Testdaten: B sieht geteilte Seiten", nid is not None)
    # 1) Ohne Aenderung: Treffer (keine neue Abfrage)
    stored = [0]
    orig = sharing._tree_store

    def counting(*a):
        stored[0] += 1
        return orig(*a)
    sharing._tree_store = counting
    try:
        trees(BU)
        stored[0] = 0
        for _ in range(3):
            trees(BU)
        ok("Unveraendert: Baum aus dem Zwischenspeicher (keine neue Abfrage)", stored[0] == 0, stored)
        with connect() as c, c:
            c.execute("UPDATE note SET body=? WHERE id=?", ('{"blocks":[]}', nid))
        trees(BU)
        ok("Nur Inhalt geaendert: weiter aus dem Zwischenspeicher", stored[0] == 0, stored)
        with connect() as c, c:
            c.execute("UPDATE note SET title=? WHERE id=(SELECT id FROM note WHERE uid=? AND id<>? ORDER BY id LIMIT 1)", ("bc-x", A, nid))
        trees(BU)
        ok("Andere Seite des Eigentuemers geaendert: neu abgefragt", stored[0] == 2, stored)
    finally:
        sharing._tree_store = orig
    # 2) Transaktion lief schon beim Fuellen (kleinere Nummer als der Stand des Eintrags), bestaetigt danach
    with connect() as t1:
        t1.execute("SELECT pg_current_xact_id()")                 # vergibt die Transaktionsnummer jetzt
        trees(BU)
        trees(BU)
        t1.execute("UPDATE note SET title=? WHERE id=?", ("bc-alte-nummer", nid))
        t1.pg.commit()
    ok("Aenderung einer schon laufenden Transaktion: sofort sichtbar", title_of(BU, nid) == "bc-alte-nummer", title_of(BU, nid))
    # 3) Unbestaetigte Aenderung einer anderen Verbindung: unsichtbar, nach dem Commit sichtbar
    with connect() as t1:
        t1.execute("UPDATE note SET title=? WHERE id=?", ("bc-offen", nid))
        a1 = title_of(BU, nid)
        t1.pg.commit()
    ok("Unbestaetigte Aenderung anderer: noch alter Stand", a1 == "bc-alte-nummer", a1)
    ok("Nach dem Commit: neuer Stand", title_of(BU, nid) == "bc-offen", title_of(BU, nid))
    # 4) Eigene Transaktion: sieht die eigene Aenderung, merkt sie sich aber nicht (Rollback)
    with connect() as c:
        c.execute("UPDATE note SET title=? WHERE id=?", ("bc-rollback", nid))
        own = dict(Access(c, BU).tree(A, meta=True).items())
        own = own[nid][1][1] if nid in own else None
        c.pg.rollback()
    ok("Eigene unbestaetigte Aenderung in derselben Transaktion sichtbar", own == "bc-rollback", own)
    ok("Nach Rollback: nichts davon im Zwischenspeicher", title_of(BU, nid) == "bc-offen", title_of(BU, nid))
    with connect() as c:
        c.execute("UPDATE note SET deleted_at=? WHERE id=?", (time.time(), nid))
        gone = nid not in Access(c, BU).tree(A)
        c.pg.rollback()
    ok("Eigener Papierkorb in der Transaktion wirkt, nach Rollback wieder da", gone and nid in dict(trees(BU)[0]))
    # 5) REPEATABLE READ: alter Stand bleibt (wie ohne Zwischenspeicher), auch wenn der Zwischenspeicher neuer ist
    with connect() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        m1 = list(Access(c, BU).tree(A, meta=True).items())
        with connect() as c2, c2:
            c2.execute("UPDATE note SET title=? WHERE id=?", ("bc-rr", nid))
        fresh = title_of(BU, nid)
        m2 = list(Access(c, BU).tree(A, meta=True).items())
        c.pg.rollback()
    ok("REPEATABLE READ: Stand der Transaktion bleibt, ausserhalb neu", m1 == m2 and fresh == "bc-rr", fresh)
    # 6) Ergebnis veraendern (Aufrufer) laesst den Zwischenspeicher unberuehrt
    before = trees(BU)
    with connect() as c:
        acc = Access(c, BU)
        a1, a2 = acc.tree(A), acc.tree(A, meta=True)
        a1.clear(), a2.clear(), a1.update({1: "edit"})
    ok("Aufrufer veraendert das Ergebnis: Zwischenspeicher unveraendert", trees(BU) == before and before[0])
    # 7) Begrenzung: groessere Baeume werden nicht gemerkt, Ergebnis gleich
    lim = sharing.TREE_CACHE_ROWS
    sharing.TREE_CACHE_ROWS = 5
    sharing._TREE_CACHE.clear()
    try:
        bad = compare("Begrenzung 5 Zeilen", http=False)
        total = sum(e[3] for e in sharing._TREE_CACHE.values())
        ok("Begrenzung: Ergebnis gleich, hoechstens 5 Zeilen gemerkt", not bad and total <= 5, (bad, total))
    finally:
        sharing.TREE_CACHE_ROWS = lim


def main():
    cleanup()
    prev_en = auth.settings(fresh=True).get("share_enabled", True)
    auth.save_setting("share_enabled", True)
    for u in (A, BU, CU, DU):
        auth.create_user(u, "Cache-Pass-1234", u.upper())
    with connect() as c, c:
        g = c.execute("INSERT INTO groups (name, description, created) VALUES (?, '', ?)", (GRP, time.time())).lastrowid
        c.execute("INSERT INTO user_group (user_id, group_id) VALUES (?, ?)", (auth.get_user(CU)["id"], g))
        c.execute("INSERT INTO user_ext_group (user_id, name) VALUES (?, ?)", (auth.get_user(DU)["id"], EXT))
    time.sleep(5.6)                                   # Einstellungen sind im Server 5 s zwischengespeichert
    try:
        r = random.Random(4711)
        gen(r, 160)
        bad = compare("Start")
        ok("Start: Baum = frisch = alt, /api/shared = frisch", not bad, bad)
        special()
        bad = compare("Nach den Sonderfaellen")
        ok("Nach den Sonderfaellen: alles gleich", not bad, bad)
        per = {}
        for step in range(300):
            op = mutate(r)
            b = compare(op, http=step % 3 == 0)
            per.setdefault(op, [0, []])
            per[op][0] += 1
            per[op][1] += b
            if step % 50 == 49:                       # zwischendurch Freigaben auffrischen (sonst sieht B bald nichts mehr)
                with connect() as c, c:
                    for nid in r.sample(live_ids(c), 3):
                        share(c, r, nid)
        for op in sorted(per):
            n, b = per[op]
            ok(f"Nach '{op}' ({n}x): Baum = frisch = alt, /api/shared = frisch", not b, b[:3])
        with connect() as c:
            sizes = [len(Access(c, u).tree(A)) for u in (BU, CU, DU)]
        ok(f"Am Ende sehen B/C/D noch Seiten ({sizes})", sum(sizes) > 0, sizes)
        ok("Keine Serverfehler", n500[0] == 0, n500)
    finally:
        auth.save_setting("share_enabled", prev_en)
        cleanup()
    n_ok = sum(res)
    print(f"\n{n_ok}/{len(res)} " + ("ALLES GRUEN" if n_ok == len(res) else "FEHLER"))


if __name__ == "__main__":
    main()
