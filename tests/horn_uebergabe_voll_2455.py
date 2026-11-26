"""2.6 (Bugs-DB 2455): "Hierher uebernehmen" bei vollem Anruf ueber einen ECHTEN Knoten - Saga laesst die Uebernahme zu
(das ersetzte Geraet zaehlt nicht, calls.join_local L4/E6), das alte Geraet bleibt aber am Knoten verbunden, bis das neue
steht. Vorher war max_peers am Knoten = Grenze des Anrufs: das neue Geraet bekam dort "knoten oder raum voll".

Laeuft in der Testumgebung Art knoten (eingebauter Knoten hinter saga-media, calls.horn + horn_all):
    tools/testumgebung/umgebung.sh lauf knoten <slot> horn_uebergabe_voll_2455.py
Medien mit `gjallarhorn horn-bot` (Go-Bein, GJALLARHORN_BIN; Weg ICE-TCP bzw. Tunnel, ohne Frame-E2E, still). Gruppe mit
drei Personen, Grenze vom Admin auf 2 gesenkt (calls.horn_video/horn_audio). Geprueft:
- A und B im Anruf (beide Bots am Knoten), C: 409 join_block full; horn.room_max 2;
- C "direkt am Knoten" (gueltiges Medien-Token fuer C, im Testprozess signiert - wie ein Geraet, das Saga umgeht):
  der Knoten lehnt ab (voll);
- A2 (zweites Geraet von A) uebernimmt: Saga 200, room_max 3, limit bleibt 2 (API), der Bot von A2 kommt am Knoten an,
  waehrend der von A noch verbunden ist (vorher: voll); C am Knoten waehrend der Uebernahme weiter abgelehnt;
- A legt auf (reason handover): room_max wieder 2, C am Knoten und ueber Saga weiter abgelehnt, A2 und B bleiben;
- horn.sync_rooms schickt "open" mit max_peers an den Knoten des Anrufs, horn._room_limits (nach dem Verbinden) ebenso
  (im Testprozess, horn.send aufgezeichnet).
Wegwerf-Konten hl55-*."""
import base64
import json
import os
import queue
import secrets
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import anrufe as A_  # noqa: E402
import auth  # noqa: E402
from db import connect  # noqa: E402
from saga import horn as HN  # noqa: E402

req, res = A_.req, A_.res
CAPS = ["reneg", "want", "bye", "screen2", "multi", "horn"]
USERS = [("hl55-a", "Anna Eins"), ("hl55-b", "Bert Zwei"), ("hl55-c", "Cora Drei")]
UP_S = 40


def ok(n, c, info=None):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n + ("" if c or info is None else " -> " + json.dumps(info, default=str)[:1500]), flush=True)


class Bots:
    """horn-bot: eine JSON-Zeile je Befehl, Ereignisse je Geraet (up/err/down)."""

    def __init__(self, exe):
        self.ev, self.lock = {}, threading.Lock()
        self.log = open(os.path.join(os.environ.get("STATE_DIR") or "/tmp", "horn-bot-2455.log"), "a")
        self.p = subprocess.Popen([exe, "horn-bot", "-insecure"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
                                  text=True, bufsize=1)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for ln in self.p.stdout:
            try:
                m = json.loads(ln)
            except ValueError:
                continue
            if m.get("id") and m.get("ev") in ("up", "err", "down"):
                with self.lock:
                    self.ev.setdefault(m["id"], queue.Queue()).put(m)

    def q(self, bid):
        with self.lock:
            return self.ev.setdefault(bid, queue.Queue())

    def send(self, **c):
        self.p.stdin.write(json.dumps(c) + "\n")
        self.p.stdin.flush()

    def add(self, bid, cand):
        path = "tcp" if cand.get("tcp") is not False else "ws"
        self.send(op="add", id=bid, cand=cand, path=path, video=False, tiles=1, device="desktop", script="still", e2e=False)

    def wait(self, bid, t=UP_S):
        """-> erstes Ereignis up/err (bzw. None nach t s)."""
        try:
            return self.q(bid).get(timeout=t)
        except queue.Empty:
            return None

    def close(self):
        try:
            self.send(op="quit")
            self.p.wait(10)
        except Exception:  # noqa: BLE001
            self.p.kill()


def cand_of(d):
    cs = ((d.get("call") or {}).get("horn") or {}).get("candidates") or []
    return cs[0] if cs else None


def payload(tok):
    pl = tok.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(pl + "=" * (-len(pl) % 4)))


def forged(cand, cid, dev):
    """Kandidat mit gueltigen Medien-Token fuer ein Geraet, das Saga nicht zugelassen hat (im Testprozess signiert)."""
    with connect() as c:
        n = HN.node_named(c, cand["node"])
    toks = {r: HN.token(n, cid, dev, r, pub=payload(t).get("pub")) for r, t in (cand.get("tokens") or {}).items()}
    return dict(cand, tokens=toks)


def room_max(cid):
    with connect() as c:
        return HN.room_max(c, cid)


def node_refused(B, cand, cid, dev, tag):
    """C mit eigenem Token direkt am Knoten -> (abgelehnt?, Ereignis)."""
    bid = f"{dev[:24]}{tag}"
    B.add(bid, forged(cand, cid, dev))
    e = B.wait(bid, 30)
    B.send(op="leave", id=bid)
    return bool(e and e.get("ev") == "err" and "voll" in (e.get("msg") or "")), e


def run(B):
    P = [A_.Person(u) for u, _ in USERS]
    A, Bp, C = P
    A2 = A.add_device()
    for p, dv in ((A, A.dev), (A, A2), (Bp, Bp.dev), (C, C.dev)):
        req("PUT", "/api/calls/caps", p.k, {"device": dv["id"], "caps": CAPS})
    s, d = req("POST", "/api/chats", A.k, {"kind": "free", "members": [Bp.uid, C.uid]})
    ok(f"freie Gruppe mit 3 ({s})", s == 200, d)
    chat = d["chat"]["id"]
    cid = secrets.token_hex(16)
    s, d = req("POST", "/api/calls", A.k, {"id": cid, "chat_id": chat, "media": "audio", "device": A.dev["id"], "caps": CAPS})
    call = (d or {}).get("call") or {}
    ca = cand_of(d or {})
    ok(f"Start ueber den Knoten, limit 2 ({s}, {call.get('topo')}, {call.get('limit')}, Kandidat {bool(ca)})",
       s == 200 and call.get("topo") == "horn" and call.get("limit") == 2 and ca, d)
    if not ca:
        return
    print("Kandidat:", json.dumps({k: v for k, v in ca.items() if k != "tokens"})[:600], flush=True)
    s, d = req("POST", f"/api/calls/{cid}/join", Bp.k, {"device": Bp.dev["id"], "role": "member", "caps": CAPS})
    cb = cand_of(d or {})
    ok(f"B tritt bei ({s})", s == 200 and cb, d)
    stop = threading.Event()

    def alive():                                       # Lebenszeichen wie die Clients (ALIVE_GAP 45 s)
        while not stop.wait(10):
            for p, dv in ((A, A.dev), (A, A2), (Bp, Bp.dev)):
                req("POST", f"/api/calls/{cid}/alive", p.k, {"device": dv["id"]})
    threading.Thread(target=alive, daemon=True).start()
    try:
        B.add(A.dev["id"], ca)
        B.add(Bp.dev["id"], cb)
        ea, eb = B.wait(A.dev["id"]), B.wait(Bp.dev["id"])
        ok(f"A und B am Knoten verbunden ({(ea or {}).get('ev')}, {(eb or {}).get('ev')})",
           (ea or {}).get("ev") == "up" and (eb or {}).get("ev") == "up", [ea, eb])

        # --- voll: C ueber Saga und direkt am Knoten -------------------------------------------------------------
        s, d = req("POST", f"/api/calls/{cid}/join", C.k, {"device": C.dev["id"], "role": "member", "caps": CAPS})
        ok(f"voll: C -> 409 join_block full, limit 2 ({s}, {d.get('join_block')}), room_max {room_max(cid)}",
           s == 409 and d.get("join_block") == "full" and d.get("limit") == 2 and room_max(cid) == 2, d)
        r, e = node_refused(B, cb, cid, C.dev["id"], "c1")
        ok(f"voll: C mit eigenem Token direkt am Knoten abgelehnt ({e})", r, e)

        # --- Uebernahme A -> A2 ------------------------------------------------------------------------------------
        s, d = req("POST", f"/api/calls/{cid}/join", A.k, {"device": A2["id"], "role": "handover", "replaces": A.dev["id"], "caps": CAPS})
        c2 = cand_of(d or {})
        me = (d or {}).get("leg") or ((d or {}).get("call") or {}).get("me") or {}
        s2, d2 = req("GET", f"/api/calls/{cid}?device={A2['id']}", A.k)
        lim = ((d2 or {}).get("call") or d2 or {}).get("limit")
        ok(f"Uebernahme bei vollem Anruf: Saga 200, room_max 3, sichtbare Grenze bleibt 2 ({s}, {room_max(cid)}, limit {lim})",
           s == 200 and c2 and room_max(cid) == 3 and lim == 2, {"join": d, "me": me})
        if c2:
            B.add(A2["id"], c2)
            e2 = B.wait(A2["id"])
            ok(f"Uebernahme: A2 kommt am Knoten an, waehrend A noch verbunden ist ({e2})", (e2 or {}).get("ev") == "up", e2)
        r, e = node_refused(B, cb, cid, C.dev["id"], "c2")
        ok(f"waehrend der Uebernahme: C direkt am Knoten weiter abgelehnt ({e})", r, e)
        s, d = req("POST", f"/api/calls/{cid}/join", C.k, {"device": C.dev["id"], "role": "member", "caps": CAPS})
        ok(f"waehrend der Uebernahme: C ueber Saga 409 full ({s}, {d.get('join_block')})", s == 409 and d.get("join_block") == "full", d)

        # --- A legt auf: wieder genau die Grenze ------------------------------------------------------------------
        s, d = req("POST", f"/api/calls/{cid}/leave", A.k, {"device": A.dev["id"], "reason": "handover"})
        B.send(op="leave", id=A.dev["id"])
        time.sleep(1.5)
        ok(f"A legt auf (handover): room_max wieder 2 ({s}, {room_max(cid)})", s == 200 and room_max(cid) == 2, d)
        r, e = node_refused(B, cb, cid, C.dev["id"], "c3")
        ok(f"nach der Uebernahme: C direkt am Knoten abgelehnt ({e})", r, e)
        s, d = req("POST", f"/api/calls/{cid}/join", C.k, {"device": C.dev["id"], "role": "member", "caps": CAPS})
        ok(f"nach der Uebernahme: C ueber Saga 409 full ({s}, {d.get('join_block')})", s == 409 and d.get("join_block") == "full", d)
        with connect() as c:
            legs = sorted(r_[0] for r_ in c.execute("SELECT device FROM chat_call_leg WHERE call_id=? AND state='active'", (cid,)).fetchall())
        ok(f"im Anruf: A2 und B ({len(legs)})", legs == sorted([A2["id"], Bp.dev["id"]]), legs)

        # --- Nachrichten an den Knoten (Testprozess, horn.send aufgezeichnet) ----------------------------------------
        sent, keep = [], HN.send
        HN.send = lambda nid, msg: sent.append((nid, msg)) or True
        try:
            with connect() as c, c:
                HN.sync_rooms(c, cid)
                nid = c.execute("SELECT node FROM chat_call_leg WHERE call_id=? AND device=?", (cid, Bp.dev["id"])).fetchone()[0]
        finally:
            HN.send = keep
        rl = HN._room_limits(nid)
        ok(f"sync_rooms: open mit max_peers 2 an den Knoten des Anrufs {sent}; nach dem Verbinden ebenso {rl}",
           len(sent) == 1 and sent[0][0] == nid and sent[0][1] == {"t": "open", "call_ref": HN.call_ref(cid, nid), "max_peers": 2}
           and {"t": "open", "call_ref": HN.call_ref(cid, nid), "max_peers": 2} in rl)
    finally:
        stop.set()
        for x in (A.dev["id"], A2["id"], Bp.dev["id"]):
            B.send(op="leave", id=x)
        req("POST", f"/api/calls/{cid}/end", Bp.k, {"device": Bp.dev["id"]})


def main():
    exe = os.environ.get("GJALLARHORN_BIN")
    if not exe or not os.access(exe, os.X_OK):
        ok("GJALLARHORN_BIN fehlt (Testumgebung Art knoten)", False)
        sys.exit(1)
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "share_group_scope", "calls")}
    auth.save_setting("chat_enabled", True)
    auth.save_setting("share_group_scope", False)
    auth.save_setting("calls", dict(prev.get("calls") or {}, enabled=True, horn=True, horn_all=True, horn_video=2, horn_audio=2))
    auth._settings_cache["v"] = None
    for u, n in USERS:
        auth.get_user(u) or auth.create_user(u, A_.PW, n)
    time.sleep(6.5)                                    # Einstellungen im Server-Prozess (Zwischenspeicher 5 s)
    B = Bots(exe)
    try:
        run(B)
    finally:
        B.close()
        for x in A_.STREAMS:
            x.close()
        for k, v in keep.items():
            auth.save_setting(k, v if v is not None else ({} if k == "calls" else True))
        auth._settings_cache["v"] = None
    print(f"\nHORN-UEBERGABE-VOLL-2455 {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
    sys.exit(0 if res and all(res) else 1)


if __name__ == "__main__":
    main()
