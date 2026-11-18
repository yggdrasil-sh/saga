"""Protokollzeile beim Sperren/Entsperren einer Seite (2.5, Feldfall "Seite gesperrt"): nur Seiten-ID und Herkunft,
nie Titel oder Inhalt; Herkunft richtig erkannt - Geraet (App-Sitzung "d<id>") vs. Web-Sitzung, deren zufaellige
Kennung (token_hex) ebenfalls mit "d" beginnen kann. Ohne Aenderung der Sperre keine Zeile. Laeuft im Prozess.
PYTHONPATH=tests/pgshim:. python tests/sperre_logzeile.py   (DATABASE_URL gesetzt)"""
import contextlib, io, json
import auth
import db
from saga import pages

res = []


def ok(name, cond, info=None):
    res.append(bool(cond))
    print(("OK  " if cond else "FAIL") + " " + name + ("" if cond else " -> " + repr(info)))


def lines(fn):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn()
    return [x for x in buf.getvalue().splitlines() if x.startswith("notes: Seite")]


db.wait_ready()
db.init_schema()
U = "sl-a"
auth.get_user(U) or auth.create_user(U, None, U)
TITLE, TEXT = "Geheimer Titel sl", "Geheimer Inhalt sl"
r = pages.note_create(U, {"title": TITLE})
nid = json.loads(r.body)["id"]
pages._note_update(U, nid, {"format": "blocks", "body": json.dumps({"blocks": [{"id": "b1", "type": "paragraph", "data": {"text": TEXT}}]})})

CASES = (("d42", "App"), ("d1f3a9c0b2e4#Tab12345678", "Web"), ("da0b1c2d3e4f", "Web"), ("0a1b2c3d4e5f#Tab12345678", "Web"),
         ("s:sl-a", "Web"), ("api", "API"), (None, "API"))
for origin, want in CASES:
    on = lines(lambda: pages._note_update(U, nid, {"locked": True}, origin))
    off = lines(lambda: pages._note_update(U, nid, {"locked": False}, origin))
    ok(f"Herkunft {origin!r} -> {want} (sperren/entsperren)", on == [f"notes: Seite {nid} gesperrt ({want})"]
       and off == [f"notes: Seite {nid} entsperrt ({want})"], (on, off))
same = lines(lambda: pages._note_update(U, nid, {"locked": False}, "d42"))
ok("Sperre unveraendert: keine Zeile", same == [], same)
both = lines(lambda: pages._note_update(U, nid, {"locked": True, "icon": "📌"}, "d42"))
ok("Zeile ohne Titel/Inhalt", both and not any(TITLE in x or TEXT in x or U in x for x in both), both)
pages._note_update(U, nid, {"locked": False}, "d42")
with db.connect() as c, c:
    c.execute("DELETE FROM note WHERE uid=?", (U,))
bad = res.count(False)
print(f"{bad} FEHLER" if bad else "ALLES GRUEN")
raise SystemExit(1 if bad else 0)
