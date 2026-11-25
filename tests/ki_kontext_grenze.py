"""KI-Kontext bei vielen sichtbaren Seiten (Bugs-DB 2349) - saga/ai.py im Prozess gegen echte DB, ohne Modell.

- visible_docs: hoechstens SCAN_MAX_SHARED geteilte Seiten, ueber alle Eigentuemer die zuletzt geaenderten; eigene Seiten
  (SCAN_MAX) unberuehrt. Papierkorb, Archiv, Vorlagen und nicht freigegebene Seiten nie (auch nicht "nachgerueckt"),
  Verweise auf Seiten ohne Zugriff bleiben maskiert. Ueberschreitung nur als Zahl (Zaehler, Log ohne Titel/Konten).
- gather: eine aeltere geteilte Seite jenseits der Grenze wird nicht durchsucht, eine neuere schon.
- Zwischenspeicher: LRU je Seite (zuletzt benutzte bleibt), Durchlauf ueber mehr Seiten als Platz verdraengt sich nicht
  selbst (Teil-Treffer statt 0 %), Obergrenze folgt dem Bedarf aktiver Personen und faellt danach auf AI_CACHE_DOCS
  zurueck, Speicher-Deckel AI_CACHE_MB haelt, cache_view nur Zahlen.

    PYTHONPATH=tests/pgshim:. python tests/ki_kontext_grenze.py
Wegwerf-Konten kz-*; raeumt auf."""
import contextlib
import io
import json
import sys
import time

import auth
import db
from db import connect
from saga import ai
from saga.sharing import names

res = []


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK   " if c else "FAIL ") + n + ("" if c else f"  {str(info)[:400]}"), flush=True)


def cleanup():
    with connect() as c, c:
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'kz-%'")
        c.execute("DELETE FROM note WHERE uid LIKE 'kz-%'")
        c.execute("DELETE FROM user_kv WHERE uid LIKE 'kz-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'kz-%'")


def blocks(text):
    return json.dumps({"blocks": [{"id": "b1", "type": "paragraph", "data": {"text": text}}]})


def page(c, uid, title, body, upd, parent=None, **kw):
    cols = ["uid", "title", "body", "created", "updated", "format", "parent_id"] + list(kw)
    vals = [uid, title, body, upd, upd, "blocks", parent] + list(kw.values())
    return c.execute(f"INSERT INTO note ({', '.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals).lastrowid


def share(c, nid, owner, target):
    c.execute("INSERT INTO note_share (note_id, owner_uid, target_type, target, role, created) VALUES (?,?,?,?,?,?)",
              (nid, owner, "user", target, "view", time.time()))


def test_limit():
    now = time.time()
    with connect() as c, c:
        own = [page(c, "kz-a", f"Eigen {i}", blocks(f"eigenes Wort{i}"), now - 5000 - i) for i in range(7)]
        priv = page(c, "kz-b", "Privat KGGEHEIM", blocks("KGGEHEIM privat"), now)            # nie freigegeben
        root = page(c, "kz-b", "Bereich", blocks("Bereich"), now - 900)
        kids = [page(c, "kz-b", f"Kind {i}", blocks(f"KGKIND{i} Bananenbrot"), now - 100 * i, root) for i in range(8)]
        link = page(c, "kz-b", "Mit Verweis", blocks(f'siehe <a href="/notes#{priv}">KGGEHEIM Titel</a>'), now - 1, root)
        trash = page(c, "kz-b", "Weg", blocks("KGPAPIERKORB"), now + 50, root, deleted_at=now)
        arch = page(c, "kz-b", "Archiv", blocks("KGARCHIV"), now + 60, root, archived=now)
        tpl = page(c, "kz-b", "Vorlage", blocks("KGVORLAGE"), now + 70, root, is_template=1)
        share(c, root, "kz-b", "kz-a")
        c2 = [page(c, "kz-c", f"Von C {i}", blocks(f"KGC{i}"), now + 10 + i) for i in range(2)]   # zweite Person, neuer
        for x in c2:
            share(c, x, "kz-c", "kz-a")
    old_max = ai.SCAN_MAX_SHARED
    ai.SCAN_MAX_SHARED = 5
    before = ai.cache_view()["shared_cut"]
    ai._STATS["logged"] = 0.0
    buf = io.StringIO()
    try:
        with connect() as c, contextlib.redirect_stdout(buf):
            docs = ai.visible_docs(c, "kz-a")
    finally:
        ai.SCAN_MAX_SHARED = old_max
    got = {d["id"] for d in docs}
    shared = [d for d in docs if d["owner"]]
    ok("eigene Seiten alle da (SCAN_MAX unberuehrt)", set(own) <= got and len(docs) - len(shared) == 7, len(docs))
    ok("hoechstens SCAN_MAX_SHARED geteilte Seiten", len(shared) == 5, len(shared))
    # sichtbar geteilt: C (2, neuestes), Kinder, Verweis-Seite, Bereich -> die 5 zuletzt geaenderten ueber alle Eigentuemer
    want = set(c2) | {link, kids[0], kids[1]}
    ok("zuletzt geaenderte ueber alle Eigentuemer", {d["id"] for d in shared} == want, sorted(d["id"] for d in shared))
    ok("Papierkorb/Archiv/Vorlage/privat nie (rueckt nicht nach)", not got & {priv, trash, arch, tpl}, got & {priv, trash, arch, tpl})
    body = next((d["body"] for d in docs if d["id"] == link), "")
    ok("Verweis auf Seite ohne Zugriff maskiert", "KGGEHEIM" not in body and f"/notes#{priv}" in body, body)
    with connect() as c:
        nm = names(c, ["kz-b", "kz-c"])
        real = {d["id"]: c.execute("SELECT uid FROM note WHERE id=?", (d["id"],)).fetchone()[0] for d in shared}
    ok("Eigentuemer der geteilten Seiten richtig", all(d["owner"] == (nm.get(real[d["id"]]) or real[d["id"]]) for d in shared)
       and set(real.values()) == {"kz-b", "kz-c"}, [(d["owner"], real[d["id"]]) for d in shared])
    ok("Zaehler Ueberschreitung", ai.cache_view()["shared_cut"] == before + 1 and ai.cache_view()["shared_cut_max"] >= 6,
       ai.cache_view())
    log = buf.getvalue()
    ok("Log-Zeile nur mit Zahlen", "SCAN_MAX_SHARED=5" in log and not any(w in log for w in ("Kind", "kz-", "Bereich", "KG")), log)
    buf = io.StringIO()
    ai.SCAN_MAX_SHARED = 5
    try:
        with connect() as c, contextlib.redirect_stdout(buf):
            ai.visible_docs(c, "kz-a")
    finally:
        ai.SCAN_MAX_SHARED = old_max
    ok("Log hoechstens alle 10 Minuten", buf.getvalue() == "", buf.getvalue())
    # gather: alte geteilte Seite (Kind 7) jenseits der Grenze nicht, neue (Kind 0) schon
    ai.SCAN_MAX_SHARED = 5
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            _t, s_old = ai.gather("kz-a", "KGKIND7", 4000)
            _t, s_new = ai.gather("kz-a", "KGKIND0", 4000)
    finally:
        ai.SCAN_MAX_SHARED = old_max
    ok("gather: aelteste geteilte Seite jenseits der Grenze nicht", kids[7] not in {s["id"] for s in s_old}, s_old)
    ok("gather: neueste geteilte Seite gefunden", kids[0] in {s["id"] for s in s_new}, s_new)
    with connect() as c:
        docs = ai.visible_docs(c, "kz-a")
    ok("ohne Ueberschreitung alle geteilten Seiten", {kids[7], root} <= {d["id"] for d in docs} and len(docs) == 7 + 12, len(docs))
    # Freigabe entzogen: kein Auszug mehr, auch nicht aus dem Zwischenspeicher (gather oben hat die Kinder zerlegt)
    with connect() as c, c:
        c.execute("DELETE FROM note_share WHERE owner_uid='kz-b' AND note_id=?", (root,))
    with connect() as c:
        docs = ai.visible_docs(c, "kz-a")
    with contextlib.redirect_stdout(io.StringIO()):
        _t, s_rev = ai.gather("kz-a", "KGKIND0 Bananenbrot", 4000)
    ok("Freigabe entzogen: Seiten weg, kein Auszug aus dem Zwischenspeicher",
       not {d["id"] for d in docs} & set(kids) and not {s["id"] for s in s_rev} & set(kids) and "KGKIND" not in _t,
       (len(docs), s_rev))


def fake(i, upd=1.0):
    return {"id": 10 ** 9 + i, "title": f"Seite {i}", "fmt": "blocks", "body": blocks(f"Wort{i} Apfel Birne"), "updated": upd}


def test_cache():
    saved = (ai.CACHE_DOCS, ai.CACHE_MB, dict(ai._DEMAND))
    with ai._IDX_LOCK:
        ai._IDX.clear()
        ai._IDX_BYTES[0] = 0
        ai._DEMAND.clear()
    try:
        ai.CACHE_DOCS = 3
        for i in range(3):
            ai._doc_index(fake(i), 1)
        ai._doc_index(fake(0), 2)                          # 0 zuletzt benutzt
        ai._doc_index(fake(3), 2)                          # verdraengt 1 (am laengsten unbenutzt), nicht 0
        ids = [k[0] - 10 ** 9 for k in ai._IDX]
        ok("LRU je Seite", ids == [2, 0, 3], ids)
        # ein Durchlauf ueber 6 Seiten bei Platz 3: verdraengt sich nicht selbst
        for i in range(10, 16):
            ai._doc_index(fake(i), 5)
        ids = [k[0] - 10 ** 9 for k in ai._IDX]
        ok("Durchlauf > Platz: erste Seiten bleiben, Rest nicht gespeichert", ids == [10, 11, 12], ids)
        for i in range(10, 16):
            ai._doc_index(fake(i), 6)
        ok("zweiter Durchlauf trifft die gespeicherten", [k[0] - 10 ** 9 for k in ai._IDX] == [10, 11, 12],
           [k[0] - 10 ** 9 for k in ai._IDX])
        # Bedarf aktiver Personen hebt die Obergrenze, danach zurueck auf AI_CACHE_DOCS
        ai._note_demand("kz-x", 8)
        for i in range(20, 28):
            ai._doc_index(fake(i), 7)
        ok("Obergrenze = Bedarf (8 Seiten)", len(ai._IDX) == 8 and ai.cache_view()["cap_docs"] == 8, ai.cache_view())
        with ai._IDX_LOCK:
            ai._DEMAND["kz-x"] = (8, time.monotonic() - ai.CACHE_ACTIVE_S - 1)
        ok("inaktive Person zaehlt nicht mehr", ai.cache_view()["cap_docs"] == 3, ai.cache_view())
        ai._doc_index(fake(30), 8)
        ok("danach wieder auf AI_CACHE_DOCS geschrumpft", len(ai._IDX) == 3, len(ai._IDX))
        # Speicher-Deckel: Bedarf hoch, aber nur Platz fuer wenige Eintraege
        one = ai._entry_bytes(ai._doc_index(fake(40), 9))
        ai._note_demand("kz-x", 1000)
        ai.CACHE_MB = (one * 4.5) / 1048576
        for i in range(41, 60):
            ai._doc_index(fake(i), 10)
        v = ai.cache_view()
        ok("Speicher-Deckel haelt", len(ai._IDX) <= 4 and ai._IDX_BYTES[0] <= one * 4.5, (len(ai._IDX), ai._IDX_BYTES[0], one))
        ok("Byte-Summe stimmt", ai._IDX_BYTES[0] == sum(e[1] for e in ai._IDX.values()))
        ok("cache_view nur Zahlen", all(isinstance(x, (int, float)) for x in v.values()), v)
        ok("Treffer liefert dieselben Auszuege", ai._doc_index(fake(59), 11) is ai._doc_index(fake(59), 12))
        # ein einzelner Eintrag groesser als der Deckel: nicht speichern und nichts verdraengen
        keep = list(ai._IDX)
        big = dict(fake(70), body=blocks(" ".join(f"Wort{i}" for i in range(int(one / 2)))))
        ai._doc_index(big, 13)
        ok("uebergrosser Eintrag verdraengt nichts", list(ai._IDX) == keep, (len(keep), len(ai._IDX)))
    finally:
        ai.CACHE_DOCS, ai.CACHE_MB = saved[0], saved[1]
        with ai._IDX_LOCK:
            ai._IDX.clear()
            ai._IDX_BYTES[0] = 0
            ai._DEMAND.clear()
            ai._DEMAND.update(saved[2])


def main():
    db.wait_ready()
    db.init_schema()
    cleanup()
    for u in ("kz-a", "kz-b", "kz-c"):
        auth.get_user(u) or auth.create_user(u, None, u)
    try:
        test_limit()
        test_cache()
    finally:
        cleanup()
    print(f"ki_kontext_grenze: {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
