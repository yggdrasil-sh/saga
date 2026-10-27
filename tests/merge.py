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

print(f"\nMERGE {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
sys.exit(0 if res and all(res) else 1)
