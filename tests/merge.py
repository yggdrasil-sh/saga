"""Saga 2.5 P3 Stufe 1: blockweises Zusammenfuehren (saga/merge.py) gegen die gemeinsamen Testvektoren
tests/vectors/merge.json, dazu eindeutige Block-IDs (notes.fix_block_ids/ensure_ids) und Eigenschaften, die fuer jede
Eingabe gelten muessen (keine doppelten IDs, Ergebnis = clean_blocks-fest). Ohne Server, ohne Datenbank."""
import json, os, random, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from saga import merge  # noqa: E402
from saga.notes import _iter_blocks, ensure_ids, fix_block_ids  # noqa: E402
from saga.pages import clean_blocks  # noqa: E402

V = json.load(open(os.path.join(ROOT, "tests", "vectors", "merge.json"), encoding="utf-8"))
res = []


def ok(n, c):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, flush=True)


def ids(doc):
    return [b.get("id") for b in _iter_blocks(doc.get("blocks") or [])]


for x in V["docs"]:
    got, info = merge.merge_doc(x["base"], x["mine"], x["theirs"], x.get("bases"), V["by"], V["now"])
    want = x["expect"]["blocks"]
    same = json.dumps(got["blocks"], sort_keys=True) == json.dumps(want, sort_keys=True)
    inf = {k: sorted(info[k]) for k in ("conflicts", "restored", "lost")} == {k: sorted(x["info"][k]) for k in ("conflicts", "restored", "lost")}
    ok(f"{x['name']}" + ("" if same else f"\n     erwartet {json.dumps(want, ensure_ascii=False)}\n     bekommen {json.dumps(got['blocks'], ensure_ascii=False)}")
       + ("" if inf else f"\n     info {info} statt {x['info']}"), same and inf)
    # symmetrisch: ohne Konflikt ist der Inhalt (Menge der Bloecke) unabhaengig davon, wer zuerst gespeichert hat
    if not x["info"]["conflicts"] and not x["info"]["lost"] and not x.get("bases"):
        rev, _ = merge.merge_doc(x["base"], x["theirs"], x["mine"], None, V["by"], V["now"])
        tx = lambda d: sorted(merge._canon(b.get("data", {}).get("text")) for b in _iter_blocks(d["blocks"]))   # noqa: E731
        ok(f"  vertauscht gleiche Bloecke und Texte: {x['name']}", sorted(ids(rev)) == sorted(ids(got)) and tx(rev) == tx(got))
for b, m, t, want, lost in V["titles"]:
    got, l2 = merge.merge_title(b, m, t)
    ok(f"Titel {b!r}+{m!r}+{t!r} -> {got!r}", got == want and l2 == lost)

# --- Text-Grundlagen -----------------------------------------------------------------------------------------------
for h in ['a<br>b <code class="hk-math">x&lt;y</code> <span class="hk-c-red"><mark class="hk-bg-blue">c</mark>d</span>',
          '<b>fett <i>kursiv</i></b> <a href="https://x.example/?a=1&amp;b=2" target="_blank" rel="noopener noreferrer">L</a>',
          '<time datetime="2026-10-10" class="hk-date">Sa.</time> &lt;3 &amp;', "\U0001F1E9\U0001F1EA กิ่ง é"]:
    ok(f"Text hin und zurueck unveraendert: {h[:40]!r}", merge.render(merge.tokens(h)) == h)
ok("Grapheme: Flagge, Hautfarbe, Thai, kombinierend",
   merge._graphemes("\U0001F1E9\U0001F1EA\U0001F44D\U0001F3FDกิ่งé") == ["\U0001F1E9\U0001F1EA", "\U0001F44D\U0001F3FD", "กิ่", "ง", "é"])
big_b = " ".join(f"w{i}" for i in range(2000))
big_m, big_t = big_b.replace("w10 ", "W10 "), big_b.replace("w1990", "W1990")
ok("Lange Texte (Wortebene) zusammengefuehrt", merge.merge_text(big_b, big_m, big_t) == big_b.replace("w10 ", "W10 ").replace("w1990", "W1990"))

# Zyklus: beide verschieben jeweils den anderen Toggle in sich hinein -> nichts geht verloren (Review integ-b)
_T = lambda i, ch: {"id": i, "type": "toggle", "data": {"text": i, "blocks": ch}}   # noqa: E731
cyc, _ = merge.merge_doc({"blocks": [_T("a", [P1 := {"id": "p", "type": "paragraph", "data": {"text": "x"}}]), _T("b", [])]},
                         {"blocks": [_T("b", [_T("a", [P1])])]}, {"blocks": [_T("a", [P1, _T("b", [])])]}, None, V["by"], V["now"])
ok("Verschieben ueber Kreuz (a in b, b in a): alle Bloecke bleiben", sorted(ids(cyc)) == ["a", "b", "p"])

# --- Eindeutige Block-IDs -------------------------------------------------------------------------------------------
doc = {"blocks": [{"type": "paragraph", "data": {"text": "a"}}, {"id": "x", "type": "paragraph", "data": {"text": "b"}},
                  {"id": "x", "type": "paragraph", "data": {"text": "c"}}, {"id": "bad id!", "type": "paragraph", "data": {"text": "d"}},
                  {"id": "k", "type": "columns", "data": {"cols": [{"blocks": [{"id": "x", "type": "paragraph", "data": {"text": "e"}}]},
                                                                    {"blocks": [{"type": "paragraph", "data": {"text": "f"}}]}]}},
                  {"id": "t", "type": "toggle", "data": {"title": "T", "blocks": [{"type": "paragraph", "data": {"text": "g"}}]}},
                  {"id": "s", "type": "synced", "data": {"sid": "AbCdEfGh12345678", "blocks": [{"id": "t", "type": "paragraph", "data": {"text": "h"}}]}}]}
d1, d2 = json.loads(json.dumps(doc)), json.loads(json.dumps(doc))
fix_block_ids(d1["blocks"], seed=7); fix_block_ids(d2["blocks"], seed=7)
all1 = ids(d1)
ok(f"Alle Bloecke (auch in Spalten/Toggle/synced) mit eindeutiger ID ({len(all1)})", len(all1) == 11 and len(set(all1)) == 11 and all(all1))
ok("Erste behaelt doppelte ID, gueltige IDs bleiben", d1["blocks"][1]["id"] == "x" and d1["blocks"][4]["id"] == "k" and d1["blocks"][5]["id"] == "t")
ok("Lesen: deterministisch je Seite (gleiche IDs fuer alle Clients)", ids(d1) == ids(d2))
d3 = json.loads(json.dumps(doc)); fix_block_ids(d3["blocks"], seed=8)
ok("... und je Seite verschieden", ids(d3) != ids(d1))
good = json.dumps(d1, ensure_ascii=False)
ok("ensure_ids: Inhalt mit eindeutigen IDs bleibt unveraendert (gleiche Zeichenkette)", ensure_ids(good, "blocks", 7) is good)
ok("ensure_ids: Text-Notizen unberuehrt", ensure_ids("hallo", "text", 7) == "hallo")
out = json.loads(clean_blocks(json.dumps(doc))[0])
ok("clean_blocks vergibt IDs fuer alle Bloecke", len(set(ids(out))) == 11 and all(ids(out)))

# --- Eigenschaften fuer zufaellige Eingaben --------------------------------------------------------------------------
rnd = random.Random(42)
WORDS = ["Haus", "Baum", "<b>fett</b>", '<span class="hk-c-red">rot</span>', "Thai กิน", "\U0001F44D", '<a href="/notes#3">S</a>', "x", "y"]


def rnd_text():
    return " ".join(rnd.choice(WORDS) for _ in range(rnd.randint(1, 5)))


def rnd_doc(n):
    bl = []
    for i in range(n):
        k = rnd.random()
        if k < 0.15:
            bl.append({"id": f"k{i}", "type": "columns", "data": {"cols": [{"blocks": [{"id": f"k{i}c{j}", "type": "paragraph", "data": {"text": rnd_text()}}]} for j in range(2)]}})
        elif k < 0.25:
            bl.append({"id": f"t{i}", "type": "toggle", "data": {"title": rnd_text(), "blocks": [{"id": f"t{i}i", "type": "paragraph", "data": {"text": rnd_text()}}]}})
        else:
            bl.append({"id": f"p{i}", "type": "paragraph", "data": {"text": rnd_text()}})
    return {"blocks": bl}


def mutate(doc, tag):
    d = json.loads(json.dumps(doc))
    flat = [(lst, i) for lst in [d["blocks"]] + [c["blocks"] for b in d["blocks"] if b["type"] == "columns" for c in b["data"]["cols"]]
            + [b["data"]["blocks"] for b in d["blocks"] if b["type"] == "toggle"] for i in range(len(lst))]
    for _ in range(rnd.randint(1, 4)):
        if not flat:
            break
        lst, i = rnd.choice(flat)
        if i >= len(lst):
            continue
        op = rnd.random()
        if op < 0.4 and "text" in lst[i]["data"]:
            lst[i]["data"]["text"] = lst[i]["data"]["text"] + " " + rnd_text()
        elif op < 0.55:
            lst.pop(i)
        elif op < 0.8:
            lst.insert(i, {"id": f"{tag}{rnd.randint(0, 10 ** 6)}", "type": "paragraph", "data": {"text": rnd_text()}})
        elif len(d["blocks"]) > 1:
            b = d["blocks"].pop(rnd.randrange(len(d["blocks"])))
            d["blocks"].insert(rnd.randrange(len(d["blocks"]) + 1), b)
    return d


bad = 0
for k in range(300):
    base = rnd_doc(rnd.randint(1, 8))
    m, t = mutate(base, "m"), mutate(base, "t")
    try:
        got, info = merge.merge_doc(base, m, t, None, V["by"], V["now"])
        all_ids = ids(got)
        assert len(all_ids) == len(set(all_ids)), "doppelte IDs"
        again = json.loads(clean_blocks(json.dumps(got))[0])
        assert ids(again) == all_ids, "clean_blocks aendert IDs"
        nm, nt = set(ids(m)) - set(ids(base)), set(ids(t)) - set(ids(base))
        assert (nm | nt) <= set(all_ids), "neue Bloecke verloren"
        same, _ = merge.merge_doc(base, m, base, None, V["by"], V["now"])
        assert json.dumps(same["blocks"], sort_keys=True) == json.dumps(m["blocks"], sort_keys=True), "deren unveraendert -> meins"
    except Exception as ex:  # noqa: BLE001
        bad += 1
        if bad <= 3:
            print("  Fall", k, type(ex).__name__, ex)
ok(f"300 zufaellige Faelle: eindeutige IDs, clean_blocks-fest, nichts Neues verloren, ohne fremde Aenderung = meins ({bad} Fehler)", bad == 0)

# --- Ueber die API (nur mit laufendem Server: HK_TEST_BASE) -----------------------------------------------------------
if os.environ.get("HK_TEST_BASE") and os.environ.get("SESSION_SECRET"):
    import base64, time, urllib.error, urllib.parse, urllib.request
    from itsdangerous import TimestampSigner
    HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
    SG = TimestampSigner(os.environ["SESSION_SECRET"])
    ckA, = (SG.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode() for u in ("hk-test-a",))

    def call(ck, m, p, b=None, hdr=None):
        h = {"Host": HOST, "Content-Type": "application/json", "Cookie": "saga=" + ck, "X-Saga": "1", **(hdr or {})}
        try:
            with urllib.request.urlopen(urllib.request.Request(os.environ["HK_TEST_BASE"] + p, data=json.dumps(b).encode() if b is not None else None,
                                                               method=m, headers=h), timeout=20) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")
    Pb = lambda i, t: {"id": i, "type": "paragraph", "data": {"text": t}}       # noqa: E731
    doc = lambda *b: json.dumps({"blocks": list(b)})                          # noqa: E731
    from db import connect
    try:
        s, d = call(ckA, "POST", "/api/notes", {"title": "Merge API"}); nid = d["id"]
        s, d = call(ckA, "PATCH", f"/api/notes/{nid}", {"body": doc(Pb("a", "Hallo Welt"), Pb("b", "zwei")), "format": "blocks", "rev": 0})
        r1 = d["rev"]
        call(ckA, "PATCH", f"/api/notes/{nid}", {"body": doc(Pb("a", "Hallo Welt"), Pb("b", "zwei!")), "format": "blocks", "rev": r1})
        s, d = call(ckA, "PATCH", f"/api/notes/{nid}", {"body": doc(Pb("a", "Hallo schoene Welt"), Pb("b", "zwei")), "format": "blocks", "rev": r1})
        ok(f"API: ohne merge weiter 409 (aeltere Apps) ({s})", s == 409 and d.get("error") == "conflict")
        s, d = call(ckA, "PATCH", f"/api/notes/{nid}", {"body": doc(Pb("a", "Hallo schoene Welt"), Pb("b", "zwei")), "format": "blocks", "rev": r1, "merge": 1,
                                                        "title": "Merge API 2"})
        got = json.loads(d.get("body") or "{}").get("blocks") or []
        ok(f"API: merge -> 200, Inhalt + Titel + rev in der Antwort ({s}, {d.get('merged')})",
           s == 200 and [x["data"]["text"] for x in got] == ["Hallo schoene Welt", "zwei!"] and d.get("title") == "Merge API 2"
           and d.get("rev") == r1 + 2 and d["merged"]["from"] == r1)
        r3 = d["rev"]
        # bases: Client hatte "zwei!!" eingereicht (r3+1 ist Antwort), schreibt weiter "zwei!!!" mit Basis = eingereichte Fassung
        s, d = call(ckA, "PATCH", f"/api/notes/{nid}", {"body": doc(Pb("a", "Hallo schoene Welt"), Pb("b", "zwei!!")), "format": "blocks", "rev": r3})
        r4 = d["rev"]
        s, d = call(ckA, "PATCH", f"/api/notes/{nid}", {"body": doc(Pb("a", "Hallo schoene Welt."), Pb("b", "zwei!!")), "format": "blocks", "rev": r4})
        r5 = d["rev"]
        s, d = call(ckA, "PATCH", f"/api/notes/{nid}", {"body": doc(Pb("a", "Hallo schoene Welt"), Pb("b", "zwei!!!")), "format": "blocks", "rev": r4,
                                                        "merge": 1, "bases": {"b": r4}})
        got = json.loads(d.get("body") or "{}").get("blocks") or []
        ok(f"API: bases je Block ({[x['data']['text'] for x in got]})", s == 200 and [x["data"]["text"] for x in got] == ["Hallo schoene Welt.", "zwei!!!"])
        # base: sent - nach zusammengefuehrter Antwort weitergeschrieben
        s, d = call(ckA, "PATCH", f"/api/notes/{nid}", {"body": doc(Pb("a", "X"), Pb("b", "zwei!!!")), "format": "blocks", "rev": d["rev"]})
        rX = d["rev"]
        call(ckA, "PATCH", f"/api/notes/{nid}", {"body": doc(Pb("a", "X"), Pb("b", "zwei!!!"), Pb("c", "von B")), "format": "blocks", "rev": rX})
        s, d = call(ckA, "PATCH", f"/api/notes/{nid}", {"body": doc(Pb("a", "X Y"), Pb("b", "zwei!!!")), "format": "blocks", "rev": rX, "merge": 1})
        rM = d["rev"]
        s, d = call(ckA, "PATCH", f"/api/notes/{nid}", {"body": doc(Pb("a", "X Y Z"), Pb("b", "zwei!!!")), "format": "blocks", "rev": rM, "merge": 1, "base": "sent"})
        got = json.loads(d.get("body") or "{}").get("blocks") or []
        ok(f"API: base sent - weitergeschrieben ohne die fremden Bloecke zu loeschen ({[x['data']['text'] for x in got]})",
           s == 200 and [x["data"]["text"] for x in got] == ["X Y Z", "zwei!!!", "von B"])
        # Basis fehlt -> 409
        with connect() as c, c:
            c.execute("DELETE FROM note_base WHERE note_id=?", (nid,))
        cur = call(ckA, "GET", f"/api/notes/{nid}")[1]["rev"]
        call(ckA, "PATCH", f"/api/notes/{nid}", {"body": doc(Pb("a", "neu")), "format": "blocks", "rev": cur})
        s, d = call(ckA, "PATCH", f"/api/notes/{nid}", {"body": doc(Pb("a", "alt")), "format": "blocks", "rev": cur - 1, "merge": 1})
        ok(f"API: Basis nicht mehr da -> 409 wie bisher ({s})", s == 409)
        # Version wiederherstellen legt einen Basisstand an: wer noch auf der Revision davor steht, wird danach
        # zusammengefuehrt statt 409 (Pruefer-Hinweis 2.5)
        s, d = call(ckA, "PATCH", f"/api/notes/{nid}", {"body": doc(Pb("v", "Version eins"), Pb("w", "zwei")), "format": "blocks", "overwrite": True})
        with connect() as c, c:                  # Version direkt sichern (Checkpoint ist gedrosselt)
            from saga.notes import _insert_version
            _insert_version(c, "hk-test-a", nid, c.execute("SELECT title, body, format FROM note WHERE id=?", (nid,)).fetchone(), time.time(), "hk-test-a")
            vid = c.execute("SELECT max(id) FROM note_version WHERE note_id=?", (nid,)).fetchone()[0]
        s, d = call(ckA, "PATCH", f"/api/notes/{nid}", {"body": doc(Pb("v", "Version zwei"), Pb("w", "zwei")), "format": "blocks", "rev": d["rev"]})
        s, d = call(ckA, "POST", f"/api/notes/{nid}/versions/{vid}/restore", {})
        rr = d.get("rev")
        with connect() as c:
            has = c.execute("SELECT 1 FROM note_base WHERE note_id=? AND rev=?", (nid, rr)).fetchone()
        ok(f"Version wiederherstellen: Basisstand der neuen Revision angelegt ({s}, rev {rr})", s == 200 and bool(has))
        s, d = call(ckA, "PATCH", f"/api/notes/{nid}", {"body": doc(Pb("v", "Version eins"), Pb("w", "zwei, danach geaendert")), "format": "blocks",
                                                        "rev": rr, "merge": 1})
        s, d = call(ckA, "PATCH", f"/api/notes/{nid}", {"body": doc(Pb("v", "Version eins!"), Pb("w", "zwei")), "format": "blocks", "rev": rr, "merge": 1})
        got = [x["data"]["text"] for x in json.loads(d.get("body") or "{}").get("blocks") or []]
        ok(f"Nach dem Wiederherstellen: veraltete rev wird zusammengefuehrt statt 409 ({s}, {got})",
           s == 200 and got == ["Version eins!", "zwei, danach geaendert"] and d.get("merged", {}).get("from") == rr)
        # GET ?since=<rev>: nur geaenderte Bloecke (unveraenderte als {"id"})
        r_now = d["rev"]
        call(ckA, "PATCH", f"/api/notes/{nid}", {"body": doc(Pb("v", "Version eins!"), Pb("w", "zwei, danach geaendert"), Pb("n", "neu")),
                                                 "format": "blocks", "rev": r_now})
        s, d = call(ckA, "GET", f"/api/notes/{nid}?since={r_now}")
        ok(f"GET ?since: delta statt body, unveraendert nur id ({d.get('delta')})", s == 200 and "body" not in d
           and d.get("delta") == {"from": r_now, "blocks": [{"id": "v"}, {"id": "w"}, Pb("n", "neu")]} and d.get("rev") == r_now + 1)
        s, d = call(ckA, "GET", f"/api/notes/{nid}?since=1")
        ok(f"GET ?since mit nicht mehr vorhandener Basis: ganzer Inhalt ({s})", s == 200 and "body" in d and "delta" not in d)
        s, d = call(ckA, "GET", f"/api/notes/{nid}?since=abc")
        ok(f"GET ?since ungueltig: ganzer Inhalt ({s})", s == 200 and "body" in d)
        cur = d["rev"]
        # Kommentare an reparierten IDs: alter Stand ohne IDs, GET liefert stabile IDs, Kommentar bleibt nach Speichern haengen
        with connect() as c, c:
            c.execute("UPDATE note SET body=? WHERE id=?", (json.dumps({"blocks": [{"type": "paragraph", "data": {"text": "ohne id"}},
                                                                                   {"type": "paragraph", "data": {"text": "auch ohne"}}]}), nid))
        n1 = call(ckA, "GET", f"/api/notes/{nid}")[1]; n2 = call(ckA, "GET", f"/api/notes/{nid}")[1]
        bids = [x["id"] for x in json.loads(n1["body"])["blocks"]]
        ok(f"Alter Stand ohne IDs: GET liefert stabile IDs ({bids})", all(bids) and len(set(bids)) == 2 and n1["body"] == n2["body"])
        s, cm = call(ckA, "POST", f"/api/notes/{nid}/comments", {"block_id": bids[1], "body": "Hinweis"})
        call(ckA, "PATCH", f"/api/notes/{nid}", {"body": n1["body"], "format": "blocks", "rev": n1["rev"]})
        th = call(ckA, "GET", f"/api/notes/{nid}/comments")[1]
        thr = th.get("threads") or th
        ok(f"Kommentar an repariertem Block bleibt nach dem Speichern zugeordnet ({s})", s == 200 and thr and thr[0]["block_id"] == bids[1] and thr[0]["block_exists"])
        # Presence mit Block
        s, d = call(ckA, "POST", "/api/presence", {"note_id": nid, "editing": True, "block": bids[0]})
        me = [u for u in d.get("users", []) if u.get("me")]
        ok(f"Presence: block und color ({me})", s == 200 and me and me[0].get("block") == bids[0] and me[0].get("color") in
           ("gray", "brown", "orange", "yellow", "green", "blue", "purple", "pink", "red"))
        call(ckA, "POST", "/api/presence", {"note_id": nid, "leave": True})
        # Datenbank-Zeile mit Formel: zusammengefuehrtes Speichern mit props -> "merged" UND "calc"/"fx_err" (Paket 1)
        s, d = call(ckA, "POST", "/api/notes", {"title": "Merge DB", "kind": "database"}); dbid = d["id"]
        s, d = call(ckA, "PATCH", f"/api/notes/{dbid}", {"schema": {"props": [{"id": "n", "name": "N", "type": "number"},
                                                                              {"id": "f", "name": "F", "type": "formula", "expr": "prop(\"n\") * 2"},
                                                                              {"id": "g", "name": "G", "type": "formula", "expr": "1 / 0"}]}})
        s, d = call(ckA, "POST", "/api/notes", {"title": "Zeile", "parent_id": dbid, "props": {"n": 2}}); rid = d["id"]
        s, d = call(ckA, "PATCH", f"/api/notes/{rid}", {"body": doc(Pb("a", "eins"), Pb("b", "zwei")), "format": "blocks", "rev": 0})
        q1 = d["rev"]
        call(ckA, "PATCH", f"/api/notes/{rid}", {"body": doc(Pb("a", "eins"), Pb("b", "zwei!")), "format": "blocks", "rev": q1})
        s, d = call(ckA, "PATCH", f"/api/notes/{rid}", {"body": doc(Pb("a", "eins."), Pb("b", "zwei")), "format": "blocks", "rev": q1,
                                                        "merge": 1, "props": {"n": 5}})
        got = [x["data"]["text"] for x in json.loads(d.get("body") or "{}").get("blocks") or []]
        ok(f"DB-Zeile: merge + props -> merged und calc/fx_err nebeneinander ({s}, {d.get('merged')}, {d.get('calc')}, {d.get('fx_err')})",
           s == 200 and got == ["eins.", "zwei!"] and d.get("merged", {}).get("from") == q1 and d.get("calc", {}).get("f") == 10
           and isinstance(d.get("fx_err"), dict) and "g" in d["fx_err"])
        s, d = call(ckA, "PATCH", f"/api/notes/{rid}", {"props": {"n": 7}})
        ok(f"DB-Zeile: nur props -> calc ohne merged ({s}, {d.get('calc')})", s == 200 and d.get("calc", {}).get("f") == 14 and "merged" not in d)
    finally:
        with connect() as c, c:
            c.execute("DELETE FROM note_comment WHERE owner_uid='hk-test-a'")
            c.execute("DELETE FROM note_base WHERE note_id IN (SELECT id FROM note WHERE uid='hk-test-a')")
            c.execute("DELETE FROM note_version WHERE uid='hk-test-a'"); c.execute("DELETE FROM note WHERE uid='hk-test-a'")
else:
    print("(kein Server - API-Teil uebersprungen)")

print(f"\nMERGE {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
sys.exit(0 if res and all(res) else 1)
