"""Goldvorlage fuer MCP tools/list (2.6, Bugs-DB 2266 K-A3): die Werkzeugliste darf sich beim Umbau auf die gemeinsame
Werkzeug-Registry (saga/tools.py) um kein Byte aendern - der claude.ai-Connector laeuft darauf.

Vorlage tests/vectors/mcp_tools_list.json (erzeugt auf dem Stand VOR dem Umbau):
  full    die ganze Liste mit allen Datenbereichen (lesbar, fuer Unterschiede)
  combos  je Kombination der Datenbereiche pages/db/tasks/files/comments/calendar (je -, r, w; w bringt r mit, wie
          apitok.norm_scopes) die SHA-256 der Antwort-Bytes von tools/list (JSON-RPC-Antwort mit id 1, gerendert wie
          die HTTP-Antwort: starlette JSONResponse samt orjson aus saga.core) und die Werkzeugnamen
Geprueft wird im Prozess (mcp.handle, alle 729 Kombinationen) und ueber HTTP gegen den laufenden Test-Server (echte
sga_-Tokens: Antwort-Bytes == im Prozess gerendert == Vorlage).

2.6 (Bugs-DB 2581, Gedaechtnis): Abschnitt "memory" - recall/remember nur mit Bereich "memory", am Ende der Liste.
  tools  die beiden Werkzeuge (lesbar); only {sha256, names} von tools/list mit ["mcp", "memory"];
  full   {sha256, names} mit allen Datenbereichen + "memory" (= full + tools, byteweise).
  Die 729 Kombinationen ohne "memory" bleiben unveraendert (Beleg: neue Werkzeuge aendern bestehende Listen nicht).

Aufruf (saga-test): tools/testumgebung/umgebung.sh lauf server <slot> mcp_gold.py
Neu schreiben (nur auf dem alten Stand!): MCP_GOLD_WRITE=<datei> ... - schreibt die Vorlage dorthin."""
import base64
import hashlib
import itertools
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

AREAS = ("pages", "db", "tasks", "files", "comments", "calendar")
GOLD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vectors", "mcp_tools_list.json")


def combos():
    """[(schluessel, bereiche)] - alle 3^6 Kombinationen, feste Reihenfolge."""
    out = []
    for lv in itertools.product("-rw", repeat=len(AREAS)):
        sc = ["mcp"]
        for a, x in zip(AREAS, lv):
            if x in "rw":
                sc.append("read:" + a)
            if x == "w":
                sc.append("write:" + a)
        out.append((",".join(f"{a}={x}" for a, x in zip(AREAS, lv)), sc))
    return out


def render(obj):
    """Bytes wie die HTTP-Antwort von POST /mcp (eine Nachricht)."""
    from saga import core
    assert core.ORJSON is not None             # import installiert orjson fuer JSONResponse wie im Server
    from fastapi.responses import JSONResponse
    return JSONResponse(obj).body


def listing(scopes):
    from saga import apitok, mcp
    tok = apitok.Tok(1, "hk-test-gold", "gold", "sga_gold", scopes, None, None)
    return mcp.handle(tok, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})


FULL_SCOPES = ["mcp"] + [p + a for a in AREAS for p in ("read:", "write:")]


def build_memory():
    """Abschnitt "memory" (2581) aus dem aktuellen Stand."""
    only = listing(["mcp", "memory"])
    full = listing(FULL_SCOPES + ["memory"])
    return {"tools": only["result"]["tools"],
            "only": {"sha256": hashlib.sha256(render(only)).hexdigest(), "names": [x["name"] for x in only["result"]["tools"]]},
            "full": {"sha256": hashlib.sha256(render(full)).hexdigest(), "names": [x["name"] for x in full["result"]["tools"]]}}


def build():
    full = listing(FULL_SCOPES)
    out = {"note": "MCP tools/list vor dem Umbau auf saga/tools.py (Bugs-DB 2266 K-A3) - nie von Hand aendern",
           "areas": list(AREAS), "full": full["result"]["tools"], "combos": {}}
    for key, sc in combos():
        r = listing(sc)
        out["combos"][key] = {"sha256": hashlib.sha256(render(r)).hexdigest(), "names": [x["name"] for x in r["result"]["tools"]]}
    return out


def compare(gold, ok):
    """Im Prozess: jede Kombination byteweise gegen die Vorlage."""
    cur = build()
    ok("Goldvorlage: volle Liste gleich (Inhalt)", cur["full"] == gold["full"],
       [x["name"] for x in cur["full"]] if cur["full"] != gold["full"] else "")
    ok("Goldvorlage: volle Liste gleich (Bytes)", render({"jsonrpc": "2.0", "id": 1, "result": {"tools": cur["full"]}})
       == render({"jsonrpc": "2.0", "id": 1, "result": {"tools": gold["full"]}}))
    ok("Goldvorlage: 729 Kombinationen", len(gold["combos"]) == 729 == len(cur["combos"]))
    bad = [k for k in gold["combos"] if cur["combos"].get(k) != gold["combos"][k]]
    ok("Goldvorlage: jede Bereichs-Kombination byteweise gleich", not bad, bad[:5])
    mem = build_memory()
    gm = gold.get("memory") or {}
    ok("Goldvorlage (2581): recall/remember gleich (Inhalt)", mem["tools"] == gm.get("tools"), [x["name"] for x in mem["tools"]])
    ok("Goldvorlage (2581): nur mcp+memory byteweise gleich", mem["only"] == gm.get("only"), mem["only"])
    ok("Goldvorlage (2581): alle Bereiche + memory = volle Liste + recall/remember am Ende (Bytes)",
       mem["full"] == gm.get("full") and mem["full"]["names"] == [x["name"] for x in gold["full"]] + mem["only"]["names"]
       and render({"jsonrpc": "2.0", "id": 1, "result": {"tools": gold["full"] + gm.get("tools", [])}}) == render(listing(FULL_SCOPES + ["memory"])),
       mem["full"]["names"][-3:])
    return cur


def dump(g):
    """Vorlage als Text: volle Liste eingerueckt, je Kombination eine Zeile (kleiner, lesbare Unterschiede)."""
    head = {k: v for k, v in g.items() if k != "combos"}
    txt = json.dumps(head, ensure_ascii=False, indent=1)[:-2]
    rows = [f" {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}" for k, v in g["combos"].items()]
    return txt + ',\n "combos": {\n' + ",\n".join(rows) + "\n }\n}\n"


# --- HTTP gegen den laufenden Test-Server ---------------------------------------------------------------------
def http_check(gold, ok):
    from itsdangerous import TimestampSigner
    B = os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8080")
    S = TimestampSigner(os.environ["SESSION_SECRET"]); HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
    A = S.sign(base64.b64encode(json.dumps({"uid": "hk-test-a", "sec_at": int(time.time())}).encode())).decode()

    def call(m, p, b=None, cookie=None, bearer=None):
        h = {"Host": HOST, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if cookie:
            h["Cookie"] = "saga=" + cookie; h["X-Saga"] = "1"
        if bearer:
            h["Authorization"] = "Bearer " + bearer; h["MCP-Protocol-Version"] = "2025-06-18"
        try:
            with urllib.request.urlopen(urllib.request.Request(B + p, data=json.dumps(b).encode() if b is not None else None,
                                                               method=m, headers=h), timeout=30) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    want = {"pages=w,db=w,tasks=w,files=w,comments=w,calendar=w": None, "pages=r,db=-,tasks=-,files=-,comments=-,calendar=-": None,
            "pages=w,db=r,tasks=-,files=-,comments=w,calendar=-": None, "pages=-,db=-,tasks=r,files=-,comments=-,calendar=r": None,
            "pages=-,db=-,tasks=-,files=-,comments=-,calendar=-": None}
    sc = dict(combos())
    sc["memory"] = ["mcp", "memory"]
    want["memory"] = None
    for key in want:
        scopes = [s for s in sc[key] if not (s.startswith("read:") and "write:" + s[5:] in sc[key])]
        st, raw = call("POST", "/api/account/api-tokens", {"name": "Gold " + key[:40], "scopes": scopes}, cookie=A)
        if st != 200:
            ok(f"HTTP: Token {key}", False, raw[:200]); continue
        tok = json.loads(raw)["token"]
        st, body = call("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, bearer=tok)
        exp = gold["memory"]["only"]["sha256"] if key == "memory" else gold["combos"][key]["sha256"]
        ok(f"HTTP tools/list {key}: Bytes == Vorlage", st == 200 and hashlib.sha256(body).hexdigest() == exp,
           body[:300])


def main():
    res = []

    def ok(n, c, info=""):
        res.append(bool(c)); print(("OK  " if c else "FAIL"), n, "" if c else str(info)[:500], flush=True)

    out = os.environ.get("MCP_GOLD_WRITE")
    mem_out = os.environ.get("MCP_GOLD_MEMORY_WRITE")      # 2581: nur den Abschnitt "memory" ergaenzen (Rest bleibt byteweise)
    if mem_out:
        with open(GOLD, encoding="utf-8") as f:
            txt = f.read()
        g = json.loads(txt)
        same = dump(g) == txt                              # Datei im dump-Format (mit oder ohne Abschnitt memory)
        g.pop("memory", None)
        ok("Vorlage: dump(gelesen) == Datei (Rest bleibt byteweise)", same)
        if same:
            g["memory"] = build_memory()
            with open(mem_out, "w", encoding="utf-8") as f:
                f.write(dump(g))
            ok(f"Abschnitt memory geschrieben: {mem_out}", True)
        print(f"\nMCP-Gold {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
        return 0 if res and all(res) else 1
    try:
        if out:
            g = build()
            with open(out, "w", encoding="utf-8") as f:
                f.write(dump(g))
            ok(f"Vorlage geschrieben: {out} ({len(g['combos'])} Kombinationen, {len(g['full'])} Werkzeuge)", len(g["combos"]) == 729)
            with open(out, encoding="utf-8") as f:
                g = json.load(f)
        else:
            with open(GOLD, encoding="utf-8") as f:
                g = json.load(f)
            compare(g, ok)
        http_check(g, ok)
    finally:
        try:
            import sqlite3
            cn = sqlite3.connect(os.environ.get("HK_TEST_DB", "/data/saga.db"))
            cn.execute("DELETE FROM api_token WHERE uid=? AND name LIKE ?", ("hk-test-a", "Gold %")); cn.commit()
        except Exception as ex:  # noqa: BLE001
            print("Aufraeumen:", ex)
    print(f"\nMCP-Gold {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
    return 0 if res and all(res) else 1


if __name__ == "__main__":
    sys.exit(main())
