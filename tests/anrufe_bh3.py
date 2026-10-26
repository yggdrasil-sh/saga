"""Bughunt 3 (Server, Anrufe 2.5): Funde mit Belegen.

1. POST /api/calls/<id>/horn nach dem Auflegen bzw. Verlassen: 404 "beendet" (ended: true) statt 409 - Web und Apps
   werten 404 als "Anruf beendet" und hoeren auf; 409 galt als "kein Knoten" (bis zu 2 min neue Versuche). Fremdes bzw.
   unbekanntes Geraet weiter 409.
2. Gruppenanruf: das Anrufobjekt (POST /api/calls, GET /api/calls/<id>, /active) traegt den Chatnamen in from.chat wie
   call.invite - Apps zeigten "Gruppenanruf" bzw. "Group call", wenn der Anruf per Abfrage oder Push ankam. 1:1 ueber
   Knoten: kein from.chat (Person). Freie Gruppe: null (Name Ende-zu-Ende-verschluesselt).

Ohne echten Knoten (Registereintrag wie tests/anrufe_horn_dm.py). Wegwerf-Konten ca-bh*, Gruppe ca-bh-team."""
import os
import secrets
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import anrufe as A_  # noqa: E402
import anrufe_horn_dm as HD  # noqa: E402
import auth  # noqa: E402
from db import connect  # noqa: E402

ok, req, res = A_.ok, A_.req, A_.res
CAPS = HD.CAPS
USERS = [("ca-bha", "Anna Bh"), ("ca-bhb", "Bert Bh"), ("ca-bhc", "Cora Bh")]
GROUP = "ca-bh-team"


def main():
    A_.cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "share_group_scope", "calls")}
    auth.save_setting("chat_enabled", True)
    auth.save_setting("share_group_scope", False)
    for u, n in USERS:
        auth.create_user(u, A_.PW, n)
    with connect() as c, c:
        auth.ensure_group(c, GROUP)
    for u, _ in USERS:
        auth.set_user_groups(auth.get_user(u)["id"], [GROUP])
    try:
        run()
    finally:
        for x in A_.STREAMS:
            x.close()
        for k, v in keep.items():
            if v is not None:
                auth.save_setting(k, v)
        auth._settings_cache["v"] = None
        HD.node(False)
        A_.cleanup()
    print(f"\nANRUFE-BH3 {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


def run():
    P = {u: A_.Person(u) for u, _ in USERS}
    A, B, Cc = (P[u] for u, _ in USERS)
    for p in P.values():
        HD.caps(p, CAPS)
    ab = A_.dm(A, B)
    HD.node(True)
    HD.setting(horn_all=True, horn_min=3, horn_min_audio=3)

    # --- 1: /horn nach dem Auflegen ---------------------------------------------------------------------------------
    cid, s, d = HD.hcall(A, ab)
    ok(f"1:1 ueber Knoten gestartet ({s}, {(d.get('call') or {}).get('topo')})", s == 200 and d["call"]["topo"] == "horn")
    HD.fresh()
    s, d = req("POST", f"/api/calls/{cid}/join", B.k, {"device": B.dev["id"], "role": "member", "caps": CAPS})
    ok(f"B nimmt an ({s})", s == 200)
    HD.fresh()
    s, d = req("POST", f"/api/calls/{cid}/horn", A.k, {"device": A.dev["id"]})
    ok(f"/horn im laufenden Anruf: 200 mit Kandidaten ({s})", s == 200 and d["horn"]["candidates"])
    s, d = req("POST", f"/api/calls/{cid}/horn", A.k, {"device": B.dev["id"]})
    ok(f"/horn fuer ein fremdes Geraet: weiter 409 ({s})", s == 409)
    s, d = req("POST", f"/api/calls/{cid}/horn", A.k, {"device": secrets.token_hex(16)})
    ok(f"/horn fuer ein unbekanntes Geraet: weiter 409 ({s})", s == 409)
    req("POST", f"/api/calls/{cid}/end", B.k, {"reason": "hangup"})
    s, d = req("POST", f"/api/calls/{cid}/horn", A.k, {"device": A.dev["id"]})
    ok(f"/horn nach dem Auflegen: 404 ended (Clients hoeren auf) ({s}, {d})", s == 404 and d.get("ended") is True)
    s, d = req("POST", f"/api/calls/{cid}/horn", B.k, {"device": B.dev["id"]})
    ok(f"/horn der auflegenden Seite: 404 ({s})", s == 404)

    # --- 2: Chatname im Anrufobjekt ----------------------------------------------------------------------------------
    s, d = req("POST", "/api/chats", A.k, {"kind": "group", "group": GROUP})
    gid = d["chat"]["id"]
    s, chd = req("GET", f"/api/chats/{gid}?device={A.dev['id']}", A.k)
    gname = chd["chat"]["name"]
    g1 = secrets.token_hex(16)
    HD.fresh()
    s, d = req("POST", "/api/calls", A.k, {"id": g1, "chat_id": gid, "media": "audio", "device": A.dev["id"], "caps": CAPS})
    ok(f"Gruppenanruf gestartet ({s}, {(d.get('call') or {}).get('kind')})", s == 200 and d["call"]["kind"] == "group")
    ok(f"POST /api/calls: from.chat = Chatname ({d['call']['from'].get('chat')!r}, {gname!r})", gname and d["call"]["from"].get("chat") == gname)
    s, d = req("GET", f"/api/calls/{g1}?device={B.dev['id']}", B.k)
    ok(f"GET /api/calls/<id> (Eingeladene): from.chat = Chatname ({d['call']['from'].get('chat')!r})", d["call"]["from"].get("chat") == gname)
    s, act = req("GET", "/api/calls/active", Cc.k)
    x = next((y for y in act.get("calls", []) if y["id"] == g1), {})
    ok(f"/api/calls/active: from.chat = Chatname ({(x.get('from') or {}).get('chat')!r})", (x.get("from") or {}).get("chat") == gname)
    req("POST", f"/api/calls/{g1}/end", A.k, {"reason": "hangup"})

    s, d = req("POST", "/api/chats", A.k, {"kind": "free", "members": ["ca-bhb", "ca-bhc"]})
    fid = d["chat"]["id"]
    g2 = secrets.token_hex(16)
    s, d = req("POST", "/api/calls", A.k, {"id": g2, "chat_id": fid, "media": "audio", "device": A.dev["id"], "caps": CAPS})
    ok(f"freie Gruppe: from.chat null (Name verschluesselt) ({s}, {(d.get('call') or {}).get('from')})",
       s == 200 and "chat" in d["call"]["from"] and d["call"]["from"]["chat"] is None)
    req("POST", f"/api/calls/{g2}/end", A.k, {"reason": "hangup"})

    cid, s, d = HD.hcall(A, ab)
    ok(f"1:1 ueber Knoten: kein from.chat ({s}, {(d.get('call') or {}).get('from')})", s == 200 and "chat" not in d["call"]["from"])
    req("POST", f"/api/calls/{cid}/end", A.k, {"reason": "hangup"})
    time.sleep(0.2)


if __name__ == "__main__":
    main()
