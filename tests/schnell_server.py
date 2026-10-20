"""Schneller (2.2, Server): Suche, Freigabe-Baum, Rueckverweise, Datenbank-Zeilen und JSON-Ausgabe liefern GENAU das,
was die bisherige Umsetzung geliefert hat - verglichen mit den alten Algorithmen (hier als Referenz kopiert) ueber
Zufallsdaten: mehrsprachige Inhalte (de/en/es/ru/th, Thai ohne Wortgrenzen), Teilwoerter, Gross/Klein, %/_/\\ im
Suchbegriff, Verweise in Text/Code/Spalten/Toggles, fuehrende Nullen, kaputtes JSON, Klartext-Seiten, zu tiefe
Verschachtelung, Papierkorb/Archiv/Vorlagen mitten im Baum, verschachtelte Freigaben an Person und Gruppe.
Jede Runde dreimal: alles frisch eingefuegt (noch nicht abgeglichen), abgeglichen, teilweise geaendert (gemischt).
Dazu: Abgleich gleichzeitig zu Aenderungen (Invariante: abgeglichen = aus dem aktuellen Inhalt abgeleitet), Aenderungen
ohne Abgleich sofort in Suche/Rueckverweisen sichtbar, Endpunkte per HTTP gegen die Referenz.
Wegwerf-Konten sv-*, raeumt auf. Umgebung wie die anderen Tests (HK_TEST_BASE, BASE_URL, SESSION_SECRET)."""
import base64, http.client, json, os, random, threading, time, urllib.parse
from itsdangerous import TimestampSigner
import auth
from db import connect
from saga import pageindex
from saga.app_api import _db_rows
from saga.databases import _first_image
from saga.notes import NOTE_META_COLS, _note_meta, _note_text
from saga.pages import _note_search, _search_hit
from saga.sharing import Access, _best, mask_links, names, viewer
from saga.core import ref_of  # 2.4 (W4): andere Konten als Handle

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
A, BU, CU = "sv-a", "sv-b", "sv-c"
GRP = "sv-team"
res, fails = [], []


def ok(n, c, detail=None):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, flush=True)
    if not c and detail is not None and len(fails) < 8:
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
    return r.status, raw


def jget(p, u, body=None, m="GET"):
    s, raw = req(m, p, u, body)
    return s, (json.loads(raw) if raw[:1] in (b"[", b"{") else raw), raw


# --- Referenz: die bisherigen Algorithmen (Stand vor 2.2, unveraendert kopiert) ------------------------------------
class OldAccess(Access):
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
        for k, v in roles.items():
            self._role.setdefault(k, v)
        if meta:
            return {r[0]: (roles[r[0]], r[3:]) for r in rows if r[0] in roles}
        return roles


def old_search(uid, q, tag):
    hits = []
    with connect() as c:
        for r in c.execute(f"SELECT {NOTE_META_COLS}, body, format FROM note WHERE uid=? AND deleted_at IS NULL AND archived IS NULL ORDER BY updated DESC", (uid,)):
            d = _search_hit(r, q, tag)
            if d:
                hits.append(d)
                if len(hits) >= 50:
                    return hits
        acc = OldAccess(c, uid)
        vis = viewer(acc)
        owners = acc.owners()
        nm = names(c, owners)
        for o in owners:
            roles = acc.tree(o)
            ids = list(roles)
            for k in range(0, len(ids), 500):
                part = ids[k:k + 500]
                for r in c.execute(f"SELECT {NOTE_META_COLS}, body, format FROM note WHERE uid=? AND deleted_at IS NULL AND id IN "
                                   f"({','.join('?' * len(part))}) ORDER BY updated DESC", (o, *part)):
                    r = tuple(r[:9]) + (mask_links(r[9], r[10], vis), r[10])
                    d = _search_hit(r, q, tag)
                    if d:
                        d.update(role=roles[r[0]], pinned=False, owner={"name": nm.get(o) or o, "username": ref_of(None, o, uid), "me": False,
                                                                             "avatar": nm.avatar(o)})
                        if d["parent_id"] not in roles:
                            d["parent_id"] = None
                        hits.append(d)
                        if len(hits) >= 50:
                            return hits
    return hits


def old_backlinks(uid, nid):
    with connect() as c:
        acc = OldAccess(c, uid)
        role = acc.role(nid)
        owner = acc.owner(nid)
        if not role or not c.execute("SELECT 1 FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, owner)).fetchone():
            return None
        rows = c.execute(f"SELECT {NOTE_META_COLS} FROM note WHERE uid=? AND id<>? AND format='blocks' AND deleted_at IS NULL "
                         "AND instr(body, ?) > 0 ORDER BY updated DESC", (owner, nid, f'/notes#{nid}\\"')).fetchall()
        if role != "owner":
            vis = acc.tree(owner)
            rows = [r for r in rows if r[0] in vis]
    out = [_note_meta(r) for r in rows]
    if role != "owner":
        for d in out:
            if d["parent_id"] not in vis:
                d["parent_id"] = None
    return out


def old_rows(uid, nid):
    with connect() as c:
        acc = OldAccess(c, uid)
        role, owner = acc.role(nid), acc.owner(nid)
        if not role:
            return None
        visible = None if role == "owner" else acc.tree(owner)
        db = c.execute("SELECT schema FROM note WHERE id=? AND uid=? AND kind='database' AND deleted_at IS NULL", (nid, owner)).fetchone()
        if not db:
            return None
        rows = c.execute("SELECT id, title, icon, props, updated, created, cover, body, format FROM note WHERE uid=? AND parent_id=? "
                         "AND deleted_at IS NULL AND is_template=0 ORDER BY sort NULLS FIRST, created", (owner, nid)).fetchall()
        tpls = c.execute("SELECT id, title, icon FROM note WHERE uid=? AND parent_id=? AND deleted_at IS NULL AND is_template=1 "
                         "ORDER BY sort NULLS FIRST, created", (owner, nid)).fetchall()
    if visible is not None:
        rows, tpls = [r for r in rows if r[0] in visible], []
    d = {"schema": json.loads(db[0]) if db[0] else {"props": []}, "templates": [{"id": t[0], "title": t[1] or "", "icon": t[2] or ""} for t in tpls],
         "rows": [{"id": r[0], "title": r[1] or "", "icon": r[2] or "", "props": json.loads(r[3]) if r[3] else {},
                   "updated": r[4], "created": r[5], "preview": r[6] or _first_image(r[7], r[8])} for r in rows]}
    d["role"] = role
    return d


# --- Zufallsdaten -----------------------------------------------------------------------------------------------
WORDS = ("Projekt projektplan Unterprojekt ANGEBOT Rechnung Kunde Straße Größe Übung äöü ÄÖÜ ß server Netzwerk "
         "project meeting Budget invoice café niño Mañana acción ÉXITO año "
         "Проект встреча Счёт ЗАДАЧА привет мир "
         "โครงการ การประชุม ใบแจ้งหนี้ สวัสดีครับ ภาษาไทย ทดสอบระบบ "
         "100% a_b back\\slash 50_% x%y C++ e-mail ohne Zugriff Seite seite "
         "İstanbul ISPARTA ıslak ΟΔΟΣ Σοφία ﬁle").split()      # Sonderfaelle beim Kleinschreiben (tuerkisches I, Schluss-Sigma)


def words(r, n):
    return " ".join(r.choice(WORDS) for _ in range(n))


def make_body(r, ids, depth=0):
    """Zufaelliges Editor.js-Dokument mit Verweisen, Bildern, Code, Spalten, Toggles."""
    bl = []
    for _ in range(r.randint(0, 6)):
        k = r.random()
        if k < 0.35:
            txt = words(r, r.randint(1, 8))
            if ids and r.random() < 0.5:
                tgt = r.choice(ids)
                form = r.random()
                href = f"/notes#0{tgt}" if form < 0.05 else f"/notes#{tgt}"
                txt += f' <a href="{href}">{words(r, 2)}</a> {words(r, 1)}'
            bl.append({"id": "%08x" % r.getrandbits(32), "type": "paragraph", "data": {"text": txt}})
        elif k < 0.45:
            bl.append({"id": "%08x" % r.getrandbits(32), "type": "header", "data": {"text": words(r, 3), "level": 2}})
        elif k < 0.55:
            bl.append({"id": "%08x" % r.getrandbits(32), "type": "image",
                       "data": {"file": {"url": "/files/%024x" % r.getrandbits(96) if r.random() < 0.8 else "https://x.example/a.png"},
                                "caption": words(r, 2)}})
        elif k < 0.62:
            code = words(r, 3) + (f' <a href="/notes#{r.choice(ids)}">code</a>' if ids else "")
            bl.append({"id": "%08x" % r.getrandbits(32), "type": "code", "data": {"code": code}})
        elif k < 0.72 and depth < 9:
            inner = json.loads(make_body(r, ids, depth + 1))["blocks"]
            bl.append({"id": "%08x" % r.getrandbits(32), "type": "toggle", "data": {"text": words(r, 2), "blocks": inner}})
        elif k < 0.80 and depth < 3:
            cols = [{"blocks": json.loads(make_body(r, ids, depth + 1))["blocks"]} for _ in range(2)]
            bl.append({"id": "%08x" % r.getrandbits(32), "type": "columns", "data": {"cols": cols}})
        else:
            bl.append({"id": "%08x" % r.getrandbits(32), "type": "list", "data": {"style": "unordered", "items": [
                {"content": words(r, 3), "items": []} for _ in range(r.randint(1, 3))]}})
    return json.dumps({"time": 1, "blocks": bl}, ensure_ascii=False)


def gen(r, n=260):
    """Seiten fuer A (Baum, Datenbanken mit Zeilen, Papierkorb/Archiv/Vorlagen) + Freigaben an B und die Gruppe."""
    now = time.time()
    ids, dbs = [], []
    with connect() as c, c:
        for i in range(n):
            par = r.choice(ids) if ids and r.random() < 0.8 else None
            if dbs and r.random() < 0.2:
                par = r.choice(dbs)
            kind = "database" if r.random() < 0.06 and par not in dbs else "page"
            fmt = "blocks"
            body = make_body(r, ids)
            x = r.random()
            if x < 0.05:
                fmt, body = "text", words(r, 6) + (f" /notes#{r.choice(ids)}\\\"" if ids else "")
            elif x < 0.07:
                body = '{"blocks": [kaputt ' + words(r, 2)
            elif x < 0.08:
                body = json.dumps([{"type": "paragraph"}])
            tags = json.dumps(r.sample(["Arbeit", "privat", "Größe", "проект", "งาน", "a_b", "100%"], r.randint(0, 3)), ensure_ascii=False)
            cur = c.execute("INSERT INTO note (uid, title, body, format, parent_id, sort, tags, kind, schema, props, created, updated, "
                            "deleted_at, archived, is_template, cover) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (A, words(r, r.randint(0, 4)), body, fmt, par, r.choice([None, r.random() * 100]),
                             tags, kind, json.dumps({"props": [], "views": []}) if kind == "database" else None,
                             json.dumps({"x": r.randint(0, 9)}) if par in dbs else None,
                             now - 5000 + i, now - r.random() * 4000 - i * 0.001,
                             now - 10 if r.random() < 0.05 else None, now - 20 if r.random() < 0.06 else None,
                             1 if r.random() < 0.05 else 0, "/files/%024x" % r.getrandbits(96) if r.random() < 0.05 else None))
            ids.append(cur.lastrowid)
            if kind == "database":
                dbs.append(cur.lastrowid)
        for nid in r.sample(ids, min(len(ids), r.randint(3, 9))):
            tt, tg = (("group", GRP) if r.random() < 0.3 else ("user", BU))
            c.execute("INSERT INTO note_share (note_id, owner_uid, target_type, target, role, created, created_by) VALUES (?,?,?,?,?,?,?) "
                      "ON CONFLICT DO NOTHING", (nid, A, tt, tg, r.choice(["view", "comment", "edit"]), now, A))
    return ids, dbs


def mutate(r, ids, share=0.3):
    with connect() as c, c:
        for nid in r.sample(ids, int(len(ids) * share)):
            k = r.random()
            if k < 0.5:
                c.execute("UPDATE note SET body=?, format='blocks', updated=? WHERE id=?", (make_body(r, ids), time.time() + r.random(), nid))
            elif k < 0.7:
                c.execute("UPDATE note SET title=? WHERE id=?", (words(r, 3), nid))
            elif k < 0.8:
                c.execute("UPDATE note SET tags=? WHERE id=?", (json.dumps(["neu-" + words(r, 1)], ensure_ascii=False), nid))
            elif k < 0.9:
                c.execute("UPDATE note SET archived=? WHERE id=?", (None if r.random() < 0.5 else time.time(), nid))
            else:
                c.execute("UPDATE note SET props=? WHERE id=?", (json.dumps({"y": 1}), nid))      # loest keinen Abgleich aus


def queries(r):
    with connect() as c:
        texts = [_note_text(f, b) for b, f in c.execute("SELECT body, format FROM note WHERE uid=? LIMIT 60", (A,)).fetchall()]
    qs = ["projekt", "PROJEKT".lower(), "unterprojekt", "größe", "straße", "проект", "встре", "ประชุม", "ไทย", "ระบ", "ñ", "a",
          "100%", "a_b", "\\", "%", "_", "x%y", "c++", "ohne zugriff", "seite ohne", "zugriff", "f ", "e s", "nichtsdagewesen",
          "i̇stanbul", "istanbul", "ı", "οδος", "οδοσ", "σοφία", "file",
          "/notes#", "notes", "kaputt", "code", "https", "e-mail"]
    for t in texts:
        if len(t) > 6:
            i = r.randint(0, len(t) - 4)
            qs.append(t[i:i + r.randint(2, 7)].lower().strip())
    return [q for q in dict.fromkeys(qs) if q][:60]


def compare_round(r, ids, dbs, label):
    # Freigabe-Baum alt/neu: B (Person) und C (Gruppe)
    for u in (BU, CU):
        with connect() as c:
            o, n = OldAccess(c, u).tree(A), Access(c, u).tree(A)
            om, nm_ = OldAccess(c, u).tree(A, meta=True), Access(c, u).tree(A, meta=True)
        ok(f"{label}: Freigabe-Baum {u} gleich ({len(n)} Seiten)", o == n, (set(o.items()) ^ set(n.items())))
        ok(f"{label}: Freigabe-Baum {u} mit Zeilen gleich, gleiche Reihenfolge", list(om.items()) == list(nm_.items()))
    # Suche alt/neu
    bad = []
    nq = 0
    for q in queries(r):
        for tag in ("", "arbeit", "größe"):
            for u in (A, BU, CU):
                o, n = old_search(u, q, tag), _note_search(u, q, tag)
                nq += 1
                if o != n:
                    bad.append((u, q, tag, [x["id"] for x in o][:10], [x["id"] for x in n][:10]))
    for tag in ("arbeit", "a_b", "100%", "проект", "งาน", "privat", "neu-projekt"):
        for u in (A, BU, CU):
            nq += 1
            if old_search(u, "", tag) != _note_search(u, "", tag):
                bad.append((u, "", tag))
    ok(f"{label}: Suche alt = neu ({nq} Anfragen, inkl. Ausschnitt/Rolle/Reihenfolge)", not bad, bad[:5])
    # Rueckverweise, Datenbank-Zeilen, Mit mir geteilt - per HTTP gegen die Referenz
    bad = []
    for nid in r.sample(ids, min(40, len(ids))):
        for u in (A, BU):
            s, d, raw = jget(f"/api/notes/{nid}/backlinks", u)
            ref = old_backlinks(u, nid)
            if (ref is None and s != 404) or (ref is not None and d != ref):
                bad.append((u, nid, s, ref, d))
    ok(f"{label}: Rueckverweise alt = neu", not bad, bad[:3])
    bad = []
    for nid in dbs:
        for u in (A, BU, CU):
            s, d, raw = jget(f"/api/notes/{nid}/rows", u)
            ref = old_rows(u, nid)
            if (ref is None and s != 404) or (ref is not None and d != ref):
                bad.append((u, nid, s, ref, d))
        ref = old_rows(A, nid)
        if _db_rows(A, nid) != (None if ref is None else {k: v for k, v in ref.items() if k != "role"}):
            bad.append(("widget", nid))
    ok(f"{label}: Datenbank-Zeilen (Vorschaubild, Rechte) alt = neu ({len(dbs)} DBs)", not bad, bad[:2])
    bad = []
    for u in (BU, CU):
        s, d, raw = jget("/api/shared", u)
        with connect() as c:
            acc = OldAccess(c, u)
            exp = [(nid, role, row[3] not in tr) for o in acc.owners() for tr in [acc.tree(o, meta=True)] for nid, (role, row) in tr.items()]
        got = [(x["id"], x["role"], x["root"]) for x in d] if s == 200 else None
        if got != exp:
            bad.append((u, exp[:5], (got or [])[:5]))
        # JSON-Ausgabe: Bytes wie json.dumps (Kommazahlen: gleicher Wert)
        if raw != json.dumps(json.loads(raw), ensure_ascii=False, separators=(",", ":")).encode():
            bad.append(("bytes", u))
    ok(f"{label}: Mit mir geteilt alt = neu, JSON wie json.dumps", not bad, bad[:2])


def check_invariant(label):
    """Fuer jede abgeglichene Seite: abgeleitete Daten = aus dem AKTUELLEN Inhalt abgeleitet."""
    bad = []
    with connect() as c:
        rows = c.execute("SELECT n.id, n.title, n.body, n.format, n.tags, d.search, d.preview, d.tags FROM note n JOIN note_derived d ON d.note_id=n.id "
                         "WHERE n.uid LIKE 'sv-%' AND NOT EXISTS (SELECT 1 FROM note_stale s WHERE s.note_id=n.id)").fetchall()
        links = {}
        for s_, d_ in c.execute("SELECT l.src, l.dst FROM note_link l JOIN note n ON n.id=l.src WHERE n.uid LIKE 'sv-%'").fetchall():
            links.setdefault(s_, set()).add(d_)
        missing = c.execute("SELECT COUNT(*) FROM note n WHERE n.uid LIKE 'sv-%' AND NOT EXISTS (SELECT 1 FROM note_derived d WHERE d.note_id=n.id) "
                            "AND NOT EXISTS (SELECT 1 FROM note_stale s WHERE s.note_id=n.id)").fetchone()[0]
    for nid, ti, bo, fm, tg, se, pv, dt in rows:
        s2, p2, t2, l2 = pageindex.derive(ti, bo, fm, tg)
        if (se, pv, dt, links.get(nid, set())) != (s2, p2, t2, l2):
            bad.append(nid)
    ok(f"{label}: abgeglichene Daten = aktueller Inhalt ({len(rows)} Seiten), keine Seite ohne Eintrag", not bad and missing == 0,
       (bad[:5], missing))


def drain():
    while pageindex.refresh(500):
        pass


def cleanup():
    from saga.userdata import delete_user_data
    for u in (A, BU, CU):
        if auth.get_user(u):
            delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM note WHERE uid LIKE 'sv-%'")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'sv-%' OR target LIKE 'sv-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'sv-%'")
        c.execute("DELETE FROM groups WHERE name LIKE 'sv-%'")


def main():
    from saga.i18n import set_lang
    set_lang("de")                                    # wie die HTTP-Anfragen (Accept-Language: de) - Platzhalter "Seite ohne Zugriff"
    cleanup()
    prev = auth.settings(fresh=True)
    prev_en = prev.get("share_enabled", True)
    auth.save_setting("share_enabled", True)
    for u in (A, BU, CU):
        auth.create_user(u, "Schnell-Pass-1234", u.upper())
    with connect() as c, c:
        g = c.execute("INSERT INTO groups (name, description, created) VALUES (?, '', ?)", (GRP, time.time())).lastrowid
        c.execute("INSERT INTO user_group (user_id, group_id) VALUES (?, ?)", (auth.get_user(CU)["id"], g))
    time.sleep(5.6)                                   # Einstellungen sind im Server 5 s zwischengespeichert
    try:
        for rnd in range(4):
            r = random.Random(1000 + rnd)
            with connect() as c, c:
                c.execute("DELETE FROM note WHERE uid LIKE 'sv-%'")
                c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'sv-%'")
            ids, dbs = gen(r, 200 + 60 * rnd)
            compare_round(r, ids, dbs, f"Runde {rnd + 1} frisch")
            drain()
            check_invariant(f"Runde {rnd + 1}")
            compare_round(r, ids, dbs, f"Runde {rnd + 1} abgeglichen")
            mutate(r, ids)
            compare_round(r, ids, dbs, f"Runde {rnd + 1} gemischt")

        # Kandidaten seitenweise: mit winzigen Seiten (3, 12, 48, ...) und mit gefilterter statt mitgeschickter id-Liste
        # (viele geteilte Seiten) muss dasselbe herauskommen
        from saga import pages
        pages.SEARCH_PAGE, pages.SEARCH_IDS_SQL = 3, 5
        mutate(random.Random(5), ids, 0.2)
        compare_round(random.Random(5), ids, dbs, "Kandidaten in Mini-Seiten, id-Filter")
        pages.SEARCH_PAGE, pages.SEARCH_IDS_SQL = 200, 2000

        # Aenderung ohne Abgleich ist sofort sichtbar (Suche, Rueckverweise), alter Text nicht mehr
        drain()
        r = random.Random(7)
        with connect() as c, c:
            tgt, src = r.sample(ids, 2)
            c.execute("UPDATE note SET body=?, format='blocks', deleted_at=NULL, archived=NULL, is_template=0 WHERE id=?",
                      (json.dumps({"blocks": [{"type": "paragraph", "data": {"text": f'frischwort-sv <a href="/notes#{tgt}">x</a>'}}]}), src))
        s, d, _ = jget("/api/notes/search", A, {"q": "frischwort-sv"}, m="POST")
        ok("Gerade gespeicherter Text sofort gefunden (ohne Abgleich)", s == 200 and [x["id"] for x in d] == [src], d)
        s, d, _ = jget(f"/api/notes/{tgt}/backlinks", A)
        ok("Gerade gesetzter Verweis sofort in den Rueckverweisen", s == 200 and src in [x["id"] for x in d], d)
        with connect() as c, c:
            c.execute("UPDATE note SET body=? WHERE id=?", (json.dumps({"blocks": []}), src))
        s, d, _ = jget("/api/notes/search", A, {"q": "frischwort-sv"}, m="POST")
        ok("Entfernter Text sofort nicht mehr gefunden", s == 200 and d == [], d)
        s, d, _ = jget(f"/api/notes/{tgt}/backlinks", A)
        ok("Entfernter Verweis sofort aus den Rueckverweisen", s == 200 and src not in [x["id"] for x in d], d)

        # Abgleich gleichzeitig zu vielen Aenderungen: danach stimmt alles (keine verlorene Aenderung)
        stop = [False]

        def worker():
            while not stop[0]:
                try:
                    pageindex.refresh(20)
                except Exception as ex:  # noqa: BLE001
                    print("refresh:", type(ex).__name__, ex)
                    stop[0] = "fehler"
        th = [threading.Thread(target=worker) for _ in range(4)]
        for t in th:
            t.start()
        rr = random.Random(9)
        for _ in range(40):
            mutate(rr, ids, rr.choice((0.02, 0.1)))
        stop[0] = stop[0] or True
        for t in th:
            t.join()
        ok("Abgleich parallel zu Aenderungen ohne Fehler", stop[0] is True)
        drain()
        check_invariant("Nach parallelem Abgleich")
        compare_round(random.Random(11), ids, dbs, "Nach parallelem Abgleich")

        # Suche ohne Index-Treffer (bisher teuerster Fall) und Thai-Teilwort per HTTP
        s, d, _ = jget("/api/notes/search", A, {"q": "zzz-nichts-sv"}, m="POST")
        ok("Suche ohne Treffer -> []", s == 200 and d == [])
        s, d, _ = jget("/api/notes/search", BU, {"q": "ประชุม"}, m="POST")
        ref = old_search(BU, "ประชุม", "")
        ok("Thai-Teilwort in geteilten Seiten (HTTP) = Referenz", s == 200 and d == ref, (d, ref))
    finally:
        auth.save_setting("share_enabled", prev_en)
        cleanup()
    n_ok = sum(res)
    print(f"\n{n_ok}/{len(res)} " + ("ALLES GRUEN" if n_ok == len(res) else "FEHLER"))


if __name__ == "__main__":
    main()
