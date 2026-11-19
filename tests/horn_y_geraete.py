"""2.5 Grundsatz Y, Geraeteregel (Entscheidung 17.11.: "30 Tage + Linux-Faehigkeit"; saga/calls.py dm_peer_check):
1:1 ueber Knoten nur, wenn jedes AKTIVE Geraet des Gegenuebers es kann.

Geprueft (Server mit Registereintrag-Knoten wie anrufe_horn_dm, Wegwerf-Konten ca-hg*):
- 2.4-App-Geraet zuletzt vor 31 Tagen gesehen -> zaehlt nicht: Anruf ueber Knoten (es klingelt am Konto); beim Annehmen
  vom alten Geraet sauber 409 join_block old (kein Haengen), das aktive Geraet nimmt an.
- dasselbe Geraet vor 10 Tagen gesehen -> 409 fallback peer (Hinweis "Gegenstelle veraltet" stimmt).
- ohne last_seen zaehlt das Anlegedatum.
- Linux-App mit caps ["multi","horn_ring"] -> faehig ohne Erreichbarkeitspruefung: Anruf ueber Knoten, call.invite mit dm.
- nur ein Browser-Geraet ohne horn -> fallback web (ohne Hinweis); Gegenueber ohne jedes Geraet -> fallback none, danach
  direkt mit dem eigentlichen Grund ("noch auf keinem Geraet eingerichtet").
- nur alte Geraete (> 30 Tage) -> diese zaehlen (2.4: fallback peer; mit horn: ueber Knoten, es klingelt dort).
- PUT /api/calls/caps und GET /api/chat/me?device= merken ein Lebenszeichen (last_seen).
- horn_ring zaehlt fuer Einladungen zu Gruppenanrufen ueber Knoten (_can_horn).
- Bugs-DB 2194: frisch gekoppeltes Geraet einer 2.5-App, das noch keine Anruf-Faehigkeiten gemeldet hat (nur GET
  /api/chat/me?device= mit X-Saga-Chat: 3 -> caps ["chat3"]): 1:1 ueber Knoten, es klingelt dort (Einladung 'ringing'),
  kein Hinweis "veraltet"; ohne Chat-Stufe 3 (App vor 2.5, caps leer) weiter fallback peer; Browser ohne Meldung weiter
  web; meldet das Geraet danach Faehigkeiten ohne horn, gilt wieder die Meldung (peer).
- im Prozess (ohne Server): F4 - Tunnel-Knoten + normaler Knoten, Anrufer mit "ws", Gegenueber-Browser ohne "frames":
  dm_peer_check nimmt den normalen Knoten statt "reach"; nur Tunnel-Knoten -> "reach"; Gruende fuer foederierte Pfade
  (dm_peer_block ohne caps: web/none/peer/None)."""
import json
import os
import secrets
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import anrufe as A_  # noqa: E402
import auth  # noqa: E402
from db import connect  # noqa: E402
from saga import horn as HN  # noqa: E402

ok, req, res = A_.ok, A_.req, A_.res
CAPS = ["reneg", "want", "bye", "screen2", "multi", "horn"]
CAPS24 = ["reneg", "want", "bye", "screen2", "multi"]
LINUX = ["multi", "horn_ring"]
CALLS = {"enabled": True, "stun": "stun:stun.example.org:3478", "turn_urls": "", "turn_mode": "rest", "turn_secret": "", "turn_user": "",
         "turn_password": "", "turn_ttl": 43200, "relay_only": False, "ring_s": 45, "horn": True, "horn_all": True, "horn_all_y": True}
NODE = "hg-knoten.example.org"
DAY = 86400
USERS = [("ca-hga", "Anna Geraet"), ("ca-hgb", "Bert Alt"), ("ca-hgc", "Cora Linux"), ("ca-hgd", "Dora Web"), ("ca-hge", "Emil Ohne"),
         ("ca-hgf", "Fritz Lange"), ("ca-hgg", "Gabi Anlage"), ("ca-hgh", "Hans Gruppe"), ("ca-hgi", "Ida Gruppe"),
         ("ca-hgj", "Jana Neu"), ("ca-hgk", "Kai Leer"), ("ca-hgl", "Lena Browser")]
NOCAPS = ("ca-hge", "ca-hgj", "ca-hgk", "ca-hgl")    # melden keine Faehigkeiten (Emil: kein Geraet; 2194: frisch gekoppelt)
CHAT3 = {"X-Saga-Chat": "3"}                        # Chat-Stufe 3 = App/Web 2.5


def node(on=True, name=NODE, builtin=0):
    with connect() as c, c:
        c.execute("DELETE FROM horn_node WHERE name=?", (name,))
        if on:
            nid = secrets.token_hex(8)
            c.execute("INSERT INTO horn_node (id, name, pub, builtin, url, udp, version, state, load, created, seen, cert) "
                      "VALUES (?,?,?,?,?,?,?,'ok',CAST(? AS JSONB),?,?,CAST(? AS JSONB))",
                      (nid, name, "dGVzdC1rbm90ZW4taGctMDAwMDAwMDAwMDAwMDAwMDA=", builtin, f"https://{name}:8443", False, HN.VERSION,
                       json.dumps({"cpu": 0.05, "legs": 0, "max_legs": 48, "state": "ok"}), time.time(), time.time(),
                       json.dumps({"mode": "alpn", "state": "self"})))
            return nid


def fresh():
    with connect() as c, c:
        c.execute("UPDATE horn_node SET seen=? WHERE name=?", (time.time(), NODE))


def caps(p, cs, dev=None):
    s, _ = req("PUT", "/api/calls/caps", p.k, {"device": (dev or p.dev)["id"], "caps": cs})
    return s == 200


def age(dev, days, created=None, name=None):
    """last_seen eines Geraets setzen (days None = NULL) - ohne Anfrage dieses Geraets danach."""
    now = time.time()
    with connect() as c, c:
        c.execute("UPDATE chat_device SET last_seen=? WHERE id=?", (None if days is None else now - days * DAY, dev["id"]))
        if created is not None:
            c.execute("UPDATE chat_device SET created=? WHERE id=?", (now - created * DAY, dev["id"]))
        if name is not None:
            c.execute("UPDATE chat_device SET name=? WHERE id=?", (name, dev["id"]))


def seen(dev):
    with connect() as c:
        r = c.execute("SELECT last_seen FROM chat_device WHERE id=?", (dev["id"],)).fetchone()
    return r[0] if r else None


def hcall(a, chat, cid=None, cs=CAPS, media="audio"):
    cid = cid or secrets.token_hex(16)
    fresh()
    s, d = req("POST", "/api/calls", a.k, {"id": cid, "chat_id": chat, "media": media, "device": a.dev["id"], "caps": cs, "via": "horn"})
    return cid, s, d


def row(cid):
    with connect() as c:
        r = c.execute("SELECT state, reason, kind, topo FROM chat_call WHERE id=?", (cid,)).fetchone()
    return dict(zip(("state", "reason", "kind", "topo"), r)) if r else None


def end(p, cid):
    req("POST", f"/api/calls/{cid}/end", p.k, {"reason": "hangup"})
    time.sleep(0.3)


def main():
    A_.cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "share_group_scope", "calls")}
    auth.save_setting("chat_enabled", True)
    auth.save_setting("share_group_scope", False)
    auth.save_setting("calls", CALLS)
    auth._settings_cache["v"] = None
    for u, n in USERS:
        auth.create_user(u, A_.PW, n)
    time.sleep(5.5)                                    # Einstellungen im Server-Prozess (Zwischenspeicher 5 s)
    try:
        run()
        inproc()
    finally:
        for x in A_.STREAMS:
            x.close()
        for k, v in keep.items():
            if v is not None:
                auth.save_setting(k, v)
        auth._settings_cache["v"] = None
        node(False)
        A_.cleanup()
    print(f"\nHORN-Y-GERAETE {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


def run():
    P = {u: A_.Person(u) for u, _ in USERS}
    A, B, Cc, D, E, F, G, H, I_, J, K, L = (P[u] for u, _ in USERS)
    for u, p in P.items():
        if u not in NOCAPS:
            caps(p, CAPS)
    node(True)
    ab, ac, ad, ae, af, ag = A_.dm(A, B), A_.dm(H, Cc), A_.dm(A, D), A_.dm(A, E), A_.dm(H, F), A_.dm(A, G)
    aj, ak, al = A_.dm(A, J), A_.dm(A, K), A_.dm(A, L)
    with connect() as c, c:                            # Emil: gar kein Chat-Geraet
        c.execute("DELETE FROM chat_device WHERE uid=?", (E.uid,))

    # --- Lebenszeichen -----------------------------------------------------------------------------------------------
    age(B.dev, 40)
    caps(B, CAPS)
    ok(f"PUT /api/calls/caps merkt ein Lebenszeichen ({round((time.time() - (seen(B.dev) or 0)) / DAY, 1)} Tage)",
       (seen(B.dev) or 0) > time.time() - 60)

    age(B.dev, 40)
    s, _ = req("GET", f"/api/chat/me?device={B.dev['id']}", B.k)
    ok(f"GET /api/chat/me?device= merkt ein Lebenszeichen ({s})", s == 200 and (seen(B.dev) or 0) > time.time() - 60)

    # --- 30-Tage-Grenze: altes 2.4-Geraet ----------------------------------------------------------------------------
    b2 = B.add_device()
    caps(B, CAPS24, dev=b2)
    age(b2, 10)
    cid, s, d = hcall(A, ab)
    ok(f"2.4-Geraet vor 10 Tagen gesehen: 409 fallback peer (Hinweis stimmt), nichts angelegt ({s}, {d.get('horn_block')})",
       s == 409 and d.get("fallback") == "direct" and d.get("horn_block") == "peer" and row(cid) is None)
    age(b2, 31)
    sb = A_.Stream(B.k)
    time.sleep(0.8)
    mb = sb.mark()
    cid, s, d = hcall(A, ab)
    k = d.get("call") or {}
    ok(f"2.4-Geraet vor 31 Tagen gesehen: zaehlt nicht, Anruf ueber Knoten ({s}, {k.get('topo')}, dm {k.get('dm')})",
       s == 200 and k.get("topo") == "horn" and k.get("dm") is True)
    inv = sb.wait("call.invite", lambda x: x.get("call_id") == cid, since=mb)
    ok(f"... es klingelt am Konto (call.invite dm, auch das alte Geraet bekommt es) ({inv and inv.get('dm')})", inv and inv.get("dm") is True)
    s, d = req("POST", f"/api/calls/{cid}/join", B.k, {"device": b2["id"], "role": "member", "caps": CAPS24})
    ok(f"... Annehmen am alten Geraet: sauber 409 join_block old, Anruf klingelt weiter ({s}, {d.get('join_block')}, {(row(cid) or {}).get('state')})",
       s == 409 and d.get("join_block") == "old" and (row(cid) or {}).get("state") == "ringing")
    fresh()
    s, d = req("POST", f"/api/calls/{cid}/join", B.k, {"device": B.dev["id"], "role": "member", "caps": CAPS})
    ok(f"... das aktive Geraet nimmt an ({s}, {(d.get('call') or {}).get('state')})", s == 200 and d["call"]["state"] == "active")
    end(B, cid)
    sb.close()

    # --- ohne last_seen: Anlegedatum ---------------------------------------------------------------------------------
    g2 = G.add_device()
    caps(G, CAPS24, dev=g2)
    age(g2, None, created=5)
    cid, s, d = hcall(A, ag)
    ok(f"ohne last_seen, vor 5 Tagen angelegt: aktiv -> fallback peer ({s}, {d.get('horn_block')})", s == 409 and d.get("horn_block") == "peer")
    age(g2, None, created=45)
    cid, s, d = hcall(A, ag)
    ok(f"ohne last_seen, vor 45 Tagen angelegt: zaehlt nicht -> ueber Knoten ({s}, {(d.get('call') or {}).get('topo')})",
       s == 200 and d["call"]["topo"] == "horn")
    end(A, cid)

    # --- Linux-App: horn_ring ----------------------------------------------------------------------------------------
    c2 = Cc.add_device()
    caps(Cc, LINUX, dev=c2)
    sc = A_.Stream(Cc.k)
    time.sleep(0.8)
    mc = sc.mark()
    cid, s, d = hcall(H, ac, media="video")
    k = d.get("call") or {}
    ok(f"Linux-App (horn_ring) neben einem Geraet mit horn: ueber Knoten, kein Hinweis ({s}, {k.get('topo')}, {d.get('horn_block')})",
       s == 200 and k.get("topo") == "horn")
    inv = sc.wait("call.invite", lambda x: x.get("call_id") == cid, since=mc)
    ok(f"... call.invite mit dm (die Linux-App klingelt, der Browser tritt bei) ({inv and inv.get('dm')})", inv and inv.get("dm") is True)
    end(H, cid)
    sc.close()
    with connect() as c, c:                            # nur noch die Linux-App
        c.execute("DELETE FROM chat_device WHERE id=?", (Cc.dev["id"],))
    cid, s, d = hcall(H, ac)
    ok(f"nur die Linux-App (horn_ring): ueber Knoten ({s}, {(d.get('call') or {}).get('topo')}, {d.get('horn_block')})",
       s == 200 and (d.get("call") or {}).get("topo") == "horn")
    end(H, cid)
    caps(Cc, ["multi"], dev=c2)                        # Linux-App vor 2.5 (nur multi)
    cid, s, d = hcall(H, ac)
    ok(f"Linux-App ohne horn_ring (aelter) und aktiv: fallback peer ({s}, {d.get('horn_block')})", s == 409 and d.get("horn_block") == "peer")

    # --- Browser ohne horn: web; kein Geraet: none ------------------------------------------------------------------
    caps(D, CAPS24)
    age(D.dev, 1, name="Firefox · Linux")
    cid, s, d = hcall(A, ad)
    ok(f"nur ein Browser ohne horn: fallback web (ohne Hinweis \"veraltet\") ({s}, {d.get('horn_block')})",
       s == 409 and d.get("fallback") == "direct" and d.get("horn_block") == "web" and row(cid) is None)
    d2 = D.add_device()
    caps(D, CAPS24, dev=d2)
    cid, s, d = hcall(A, ad)
    ok(f"Browser ohne horn + aktive 2.4-App: fallback peer ({s}, {d.get('horn_block')})", s == 409 and d.get("horn_block") == "peer")
    cid, s, d = hcall(A, ae)
    ok(f"Gegenueber ohne Chat-Geraet: fallback none ({s}, {d.get('horn_block')})", s == 409 and d.get("horn_block") == "none" and row(cid) is None)
    s2, d2_ = A_.call(A, E, ae, cid=cid, devs=[])[1:]
    ok(f"... direkt danach: eigentlicher Grund ({s2}, {d2_.get('error')})", s2 == 409 and "keinem Gerät" in (d2_.get("error") or ""))

    # --- nur alte Geraete --------------------------------------------------------------------------------------------
    # Pruefer pruef-yg: hat das Gegenueber NUR alte Geraete, zaehlen diese - sonst klingelte bei ihm gar nichts (Einladung
    # 'old': kein Geraet mit horn), ein direkter Anruf erreicht die alte App aber
    caps(F, CAPS24)
    age(F.dev, 60)
    cid, s, d = hcall(H, af)
    ok(f"nur ein 2.4-Geraet, vor 60 Tagen gesehen: fallback peer, nichts angelegt ({s}, {d.get('horn_block')})",
       s == 409 and d.get("fallback") == "direct" and d.get("horn_block") == "peer" and row(cid) is None)
    caps(F, CAPS)
    age(F.dev, 60)
    sf = A_.Stream(F.k)
    time.sleep(0.8)
    mf = sf.mark()
    cid, s, d = hcall(H, af)
    ok(f"nur ein Geraet mit horn, vor 60 Tagen gesehen: ueber Knoten ({s}, {(d.get('call') or {}).get('topo')})",
       s == 200 and d["call"]["topo"] == "horn")
    inv = sf.wait("call.invite", lambda x: x.get("call_id") == cid, since=mf)
    with connect() as c:
        ist = c.execute("SELECT state FROM chat_call_invite WHERE call_id=? AND who=?", (cid, F.uid)).fetchone()
    ok(f"... es klingelt dort (Einladung {ist and ist[0]}, call.invite {bool(inv)})", bool(inv) and ist and ist[0] == "ringing")
    end(H, cid)
    sf.close()

    # --- Gruppenanruf ueber Knoten: horn_ring wird eingeladen --------------------------------------------------------
    with connect() as c:
        from saga import calls as CL
        caps(I_, LINUX)
        ok(f"_can_horn: horn_ring zaehlt ({CL._can_horn(c, I_.uid)}), nur multi nicht ({CL._can_horn(c, D.uid)})",
           CL._can_horn(c, I_.uid) and not CL._can_horn(c, D.uid))

    fresh2194(A, J, K, L, aj, ak, al)


def dcaps(dev):
    with connect() as c:
        r = c.execute("SELECT caps FROM chat_device WHERE id=?", (dev["id"],)).fetchone()
    return json.loads(r[0]) if r else None


def fresh2194(A, J, K, L, aj, ak, al):
    """Bugs-DB 2194: erster 1:1 an ein frisch gekoppeltes 2.5-Geraet ohne gemeldete Faehigkeiten."""
    s, _ = req("GET", f"/api/chat/me?device={J.dev['id']}", J.k, hdr=CHAT3)
    ok(f"2194: GET /api/chat/me?device= mit Chat-Stufe 3 merkt \"chat3\" ({s}, {dcaps(J.dev)})", s == 200 and dcaps(J.dev) == ["chat3"])
    sj = A_.Stream(J.k)
    time.sleep(0.8)
    mj = sj.mark()
    cid, s, d = hcall(A, aj)
    k = d.get("call") or {}
    ok(f"2194: frisch gekoppelt, noch ohne Meldung: ueber Knoten statt fallback peer ({s}, {k.get('topo')}, {d.get('horn_block')})",
       s == 200 and k.get("topo") == "horn" and k.get("dm") is True)
    inv = sj.wait("call.invite", lambda x: x.get("call_id") == cid, since=mj)
    with connect() as c:
        ist = c.execute("SELECT state FROM chat_call_invite WHERE call_id=? AND who=?", (cid, J.uid)).fetchone()
    ok(f"2194: ... es klingelt dort (Einladung {ist and ist[0]}, call.invite {bool(inv)})", bool(inv) and ist and ist[0] == "ringing")
    fresh()
    s, d = req("POST", f"/api/calls/{cid}/join", J.k, {"device": J.dev["id"], "role": "member", "caps": CAPS})
    ok(f"2194: ... das Geraet nimmt mit seinen caps an ({s}, {(d.get('call') or {}).get('state')})",
       s == 200 and (d.get("call") or {}).get("state") == "active")
    end(J, cid)
    sj.close()
    ok(f"2194: ... caps danach die gemeldeten (+chat3) ({dcaps(J.dev)})", "horn" in (dcaps(J.dev) or []) and "chat3" in (dcaps(J.dev) or []))
    # Gegenprobe: App vor 2.5 (ohne Chat-Stufe 3) - caps bleiben leer, Hinweis "veraltet" stimmt
    s, _ = req("GET", f"/api/chat/me?device={K.dev['id']}", K.k, hdr={"X-Saga-Chat": "2"})
    cid, s, d = hcall(A, ak)
    ok(f"2194: App vor 2.5 ohne Meldung (caps {dcaps(K.dev)}): weiter fallback peer ({s}, {d.get('horn_block')})",
       s == 409 and d.get("horn_block") == "peer" and row(cid) is None)
    # Browser ohne Meldung: ob er es kann, haengt am Browser - weiter web (ohne Hinweis)
    age(L.dev, 0, name="Chrome · Mac")
    s, _ = req("GET", f"/api/chat/me?device={L.dev['id']}", L.k, hdr=CHAT3)
    cid, s, d = hcall(A, al)
    ok(f"2194: Browser mit chat3 ohne Meldung ({dcaps(L.dev)}): weiter fallback web ({s}, {d.get('horn_block')})",
       s == 409 and d.get("horn_block") == "web")
    # meldet ein Geraet Faehigkeiten (ohne horn), gilt die Meldung
    caps(J, CAPS24)
    cid, s, d = hcall(A, aj)
    ok(f"2194: danach Meldung ohne horn ({dcaps(J.dev)}): fallback peer ({s}, {d.get('horn_block')})",
       s == 409 and d.get("horn_block") == "peer")


def inproc():
    """F4 und Gruende ohne Server (Knotenzustand _LIVE dieses Prozesses)."""
    from saga import calls as CL
    from saga import chat as CH
    auth._settings_cache["v"] = None
    st = auth.settings(fresh=True)
    auth.save_setting("calls", dict(CALLS, horn_builtin=True))
    auth._settings_cache["v"] = None
    os.environ["HORN_TLS_SELF"] = "1"
    node(False)
    tun = node(True, name="hg-tunnel.example.org", builtin=1)
    nrm = node(True, name="hg-normal.example.org")
    try:
        with connect() as c:
            HN._LIVE[tun] = {"tunnel": True, "at": time.time(), "frames": False}
            uid = "ca-hgx"
            auth.create_user(uid, A_.PW, "Xaver Safari")
        now = time.time()
        with connect() as c, c:
            for i, (nm, cs, ls) in enumerate([("Safari · Mac", CAPS, now), ("Testgeraet", CAPS + ["ws"], now)]):
                c.execute("INSERT INTO chat_device (id, uid, name, enc_pub, sign_pub, sig, created, last_seen, caps) VALUES (?,?,?,?,?,?,?,?,?)",
                          (secrets.token_hex(16), uid, nm, "x", "x", "x", now, ls, json.dumps(cs)))
        with connect() as c:
            ns = HN.ready_nodes(c, ws="ws")
            ok(f"im Prozess: beide Knoten bereit, Tunnel-Knoten zuerst fuer ein Geraet mit ws ({[n['name'] for n in ns]})",
               len(ns) == 2 and ns[0]["id"] == tun)
            why, n = CL.dm_peer_check(c, uid, CAPS + ["ws"])
            ok(f"F4: Safari ohne frames beim Gegenueber -> normaler Knoten statt reach ({why}, {n and n['name']})",
               why is None and n and n["id"] == nrm)
            why, n = CL.dm_peer_check(c, uid, CAPS)
            ok(f"Anrufer ohne ws: normaler Knoten ({why}, {n and n['name']})", why is None and n and n["id"] == nrm)
        node(False, name="hg-normal.example.org")
        with connect() as c:
            why, n = CL.dm_peer_check(c, uid, CAPS + ["ws"])
            ok(f"nur der Tunnel-Knoten: reach ({why})", why == "reach" and n is None)
            with c:
                c.execute("UPDATE chat_device SET last_seen=? WHERE uid=? AND name='Safari · Mac'", (now - 31 * DAY, uid))
            why, n = CL.dm_peer_check(c, uid, CAPS + ["ws"])
            ok(f"... Safari 31 Tage nicht gesehen: zaehlt nicht -> Tunnel-Knoten ({why}, {n and n['name']})", why is None and n and n["id"] == tun)
            with c:
                c.execute("UPDATE chat_device SET caps=?, last_seen=? WHERE uid=? AND name='Safari · Mac'", (json.dumps(LINUX), now, uid))
            why, n = CL.dm_peer_check(c, uid, CAPS + ["ws"])
            ok(f"horn_ring ohne Erreichbarkeitspruefung: Tunnel-Knoten ({why}, {n and n['name']})", why is None and n and n["id"] == tun)
            ok(f"foederiert (ohne caps): horn_ring + horn -> None ({CL.dm_peer_block(c, uid)})", CL.dm_peer_block(c, uid) is None)
            with c:
                c.execute("UPDATE chat_device SET caps=? WHERE uid=? AND name='Safari · Mac'", (json.dumps(CAPS24), uid))
            ok(f"foederiert: Browser ohne horn -> web ({CL.dm_peer_block(c, uid)})", CL.dm_peer_block(c, uid) == "web")
            with c:
                c.execute("UPDATE chat_device SET caps=? WHERE uid=? AND name='Testgeraet'", (json.dumps(CAPS24), uid))
            ok(f"foederiert: + App ohne horn -> peer ({CL.dm_peer_block(c, uid)})", CL.dm_peer_block(c, uid) == "peer")
            with c:
                c.execute("DELETE FROM chat_device WHERE uid=?", (uid,))
            ok(f"foederiert: kein Geraet -> none ({CL.dm_peer_block(c, uid)})", CL.dm_peer_block(c, uid) == "none")
            ok(f"DM_ACTIVE_S = chat.ACTIVE_S = 30 Tage ({CL.DM_ACTIVE_S // DAY})", CL.DM_ACTIVE_S == CH.ACTIVE_S == 30 * DAY)
            pend = [CL._caps_pending('["chat3"]', "Pixel 9"), CL._caps_pending(["chat3"], ""), CL._caps_pending("[]", "Pixel 9"),
                    CL._caps_pending('["chat3","multi"]', "Pixel 9"), CL._caps_pending('["chat3"]', "Firefox · Linux"),
                    CL._caps_pending(None, "x")]
            ok(f"2194: _caps_pending nur fuer caps = [chat3] und kein Browser ({pend})", pend == [True, True, False, False, False, False])
            with c:                                    # Pruefer 2194: nur ein frisch gekoppeltes 2.5-Geraet (caps [chat3])
                c.execute("INSERT INTO chat_device (id, uid, name, enc_pub, sign_pub, sig, created, last_seen, caps) VALUES (?,?,?,?,?,?,?,?,?)",
                          (secrets.token_hex(16), uid, "Pixel 9", "x", "x", "x", now, now, '["chat3"]'))
            why, n = CL.dm_peer_check(c, uid, CAPS + ["ws"])
            ok(f"2194: nur [chat3]: Knoten-Einladung (_can_horn {CL._can_horn(c, uid)}), keine Mesh-Gruppe (_can_multi {CL._can_multi(c, uid)}), "
               f"wie horn_ring ohne Erreichbarkeitspruefung ({why}, {n and n['name']}), foederiert None ({CL.dm_peer_block(c, uid)})",
               CL._can_horn(c, uid) and not CL._can_multi(c, uid) and why is None and n and n["id"] == tun and CL.dm_peer_block(c, uid) is None)
            with c:
                c.execute("UPDATE chat_device SET name='Chrome · Linux' WHERE uid=?", (uid,))
            ok(f"2194: Browser mit [chat3] zaehlt nicht (_can_horn {CL._can_horn(c, uid)}, {CL.dm_peer_block(c, uid)})",
               not CL._can_horn(c, uid) and CL.dm_peer_block(c, uid) == "web")
            with c:
                c.execute("DELETE FROM chat_device WHERE uid=?", (uid,))
    finally:
        HN._LIVE.pop(tun, None)
        node(False, name="hg-tunnel.example.org")
        node(False, name="hg-normal.example.org")
        if st.get("calls") is not None:
            auth.save_setting("calls", st["calls"])


if __name__ == "__main__":
    main()
