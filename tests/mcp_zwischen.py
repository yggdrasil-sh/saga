"""MCP-Schreibwege (2.5, Pruefung pruef-mcp): Zwischenschreiber zwischen Lesen und Schreiben im Server.

Laeuft im Prozess (wie tests/kalender_modul.py) gegen die Datenbank aus DATABASE_URL: api_v1.blocks_edit (Weg von
update_block, set_checkbox, add_widget, update_widget, embed_database) liest die Seite, aendert die Bloecke und schreibt
mit der gelesenen rev. Schreibt das Web genau dazwischen, muss 409 kommen und die Web-Aenderung bleiben."""
import json

import db

db.wait_ready()
from saga import api_v1, blockedit  # noqa: E402
from saga.api_v1 import Fail  # noqa: E402
from saga.pages import _Reply, _note_update, note_create, note_get, note_trash  # noqa: E402

U = "hk-test-a"
res = []


def ok(n, c, info=None):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, "" if c or info is None else f"-> {str(info)[:300]}")


class Tok:
    uid, roots = U, None

    def has(self, s):
        return s in ("read:pages", "write:pages")


def relay(r):
    try:
        return api_v1._relay(r)
    except _Reply as x:
        return api_v1._relay(x.resp)


def body_of(nid):
    return json.loads(api_v1._relay(note_get(U, nid))["body"])["blocks"]


nid = relay(note_create(U, {"title": "MCP Zwischenschreiber"}))["id"]
try:
    relay(_note_update(U, nid, {"format": "blocks", "body": json.dumps({"blocks": [
        {"id": "h1", "type": "header", "data": {"text": "Status", "level": 2}},
        {"id": "p1", "type": "paragraph", "data": {"text": "alt"}}]})}, "web"))

    def web_dazwischen(blocks):
        cur = api_v1._relay(note_get(U, nid))
        b = json.loads(cur["body"])
        b["blocks"].append({"id": "web1", "type": "paragraph", "data": {"text": "vom Web dazwischen"}})
        relay(_note_update(U, nid, {"format": "blocks", "body": json.dumps(b), "rev": cur["rev"]}, "web"))
        return blockedit.apply(blocks, api_v1.md_to_blocks("neu"), None, "p1", None)
    try:
        api_v1.blocks_edit(Tok(), nid, web_dazwischen)
        st = 200
    except Fail as f:
        st = f.status
    ids = [b["id"] for b in body_of(nid)]
    ok("blocks_edit: Web schreibt zwischen Lesen und Schreiben -> 409", st == 409, st)
    ok("blocks_edit: Web-Aenderung bleibt, MCP-Aenderung nicht still darueber", "web1" in ids
       and next(b for b in body_of(nid) if b["id"] == "p1")["data"]["text"] == "alt", ids)
    st, d = api_v1.blocks_edit(Tok(), nid, lambda bl: blockedit.apply(bl, api_v1.md_to_blocks("neu"), None, "p1", None))
    ok("blocks_edit ohne Zwischenschreiber: geschrieben, web1 bleibt", st == 200 and "web1" in [b["id"] for b in body_of(nid)]
       and next(b for b in body_of(nid) if b["id"] == "p1")["data"]["text"] == "neu", d)
finally:
    note_trash(U, nid, "web")

print(f"\nMCP-ZWISCHEN {sum(res)}/{len(res)} - " + ("ALLES GRUEN" if all(res) else "FEHLER"))
