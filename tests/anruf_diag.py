"""Anruf-Diagnose (2.5, saga/calldiag.py): POST /api/calls/diag.

Geprueft: Feature calldiag1 in /api/version (nur wenn an), Rechte (Teilnehmende ja; Unbeteiligte, unbekannte Anruf-ID,
ohne Anmeldung bzw. ohne X-Saga -> 404/401), strenge Pruefung (falsche Typen, Laengen, zu viele samples/events, Werte
ausserhalb -> 400; unbekannte Felder werden verworfen), Groesse (> 32 KB -> 413), Ratenbegrenzung (1 POST je 15 s je Konto
und Anruf -> 429, anderes Konto bzw. anderer Anruf nicht betroffen), Log-Zeile "calldiag" (eine je POST, Konto nur als
Hash, kein Konto-/Anzeigename, keine IP-Adresse, unbekannte Felder nicht im Log), Admin-Schalter calls.diag (aus -> 404,
Feature fehlt; Admin-Oberflaeche hat den Schalter). Wegwerf-Konten ca-dg*; raeumt auf."""
import json
import os
import re
import secrets
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import anrufe as A_  # noqa: E402  (Helfer: Person, dm, call, req, ...)
import auth  # noqa: E402

ok, req, res = A_.ok, A_.req, A_.res
USERS = [("ca-dga", "Anna Diagnose"), ("ca-dgb", "Bert Diagnose"), ("ca-dgc", "Cora Diagnose")]
LOG = os.path.join(os.environ.get("STATE_DIR", "/tmp/hkstage"), "server.log")


def body(cid, **kw):
    b = {"v": 1, "call": cid, "plat": "android", "app": "2.5.0-rc.3", "path": "horn", "transport": "udp", "node": "horn-1.example.org",
         "samples": [{"t": 3000, "dir": "in", "src": "v1", "rid": "", "fps": 24, "h": 540, "kbps": 812.5, "lost": 3, "nack": 4, "pli": 1,
                      "freeze": 1, "kf": 1, "rtt": 38, "qlr": "", "enc": ""},
                     {"t": 3000, "dir": "out", "src": "v0", "rid": "h", "fps": 30, "h": 720, "kbps": 1400, "lost": 0, "nack": 0, "pli": 2,
                      "freeze": 0, "kf": 2, "rtt": 38, "qlr": "bandwidth", "enc": "MediaCodec (c2.qti.avc.encoder)"}],
         "events": [{"t": 2500, "k": "layers", "d": "v est=1800 s0:m3@ab12"}, {"t": 2900, "k": "quality", "d": "good>mid"}]}
    b.update(kw)
    return b


def logtxt():
    try:
        return open(LOG, encoding="utf-8", errors="replace").read()
    except OSError:
        return None


def feats():
    return req("GET", "/api/version")[1].get("features") or []


def main():
    A_.cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "share_group_scope", "calls")}
    auth.save_setting("chat_enabled", True)
    auth.save_setting("share_group_scope", False)
    auth.save_setting("calls", {**(prev.get("calls") or {}), "enabled": True, "diag": True})
    for u, n in USERS:
        auth.create_user(u, A_.PW, n)
    auth.create_user("ca-adm", A_.PW, "Admin Diagnose", is_admin=True)     # Admin-Bereich (A_.admin)
    time.sleep(A_.WAIT)                                # Einstellungen im Server (5 s zwischengespeichert)
    try:
        run()
    finally:
        for x in A_.STREAMS:
            x.close()
        for k, v in keep.items():
            if v is not None:
                auth.save_setting(k, v)
        auth._settings_cache["v"] = None
        A_.cleanup()
    print(f"\nANRUF-DIAG {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


def run():
    P = {u: A_.Person(u) for u, _ in USERS}
    A, B, Cc = (P[u] for u, _ in USERS)
    ok(f"/api/version: Feature calldiag1 (Standard an) ({feats()})", "calldiag1" in feats())
    ok("Standard in auth.DEFAULTS: calls.diag = True", auth.DEFAULTS["calls"].get("diag") is True)
    ab = A_.dm(A, B)
    cid, s, d = A_.call(A, B, ab)
    ok(f"Anruf A -> B ({s})", s == 200)
    n0 = len(logtxt() or "")

    # --- Rechte -----------------------------------------------------------------------------------------------------
    s, d = req("POST", "/api/calls/diag", Cc.k, body(cid))
    ok(f"Unbeteiligte: 404 ({s})", s == 404)
    s, d = req("POST", "/api/calls/diag", A.k, body(secrets.token_hex(16)))
    ok(f"unbekannte Anruf-ID: 404 ({s})", s == 404)
    s, d = req("POST", "/api/calls/diag", None, body(cid))
    ok(f"ohne Anmeldung: 401 ({s})", s == 401)
    s, d = req("POST", "/api/calls/diag", A.k, body(cid), hdr={"X-Saga": "0"})
    ok(f"ohne X-Saga-Kopf (Schutz wie andere JSON-POSTs): 401 ({s})", s == 401)

    # --- Pruefung -----------------------------------------------------------------------------------------------------
    bad = [("v=2", body(cid, v=2)), ("plat unbekannt", body(cid, plat="windows")), ("path", body(cid, path="sfu")),
           ("transport", body(cid, transport="quic")), ("call kein Hex", body(cid, call="x" * 32)),
           ("app zu lang", body(cid, app="1" * 41)), ("node mit Leerzeichen", body(cid, node="a b")),
           ("samples kein Array", body(cid, samples={})), ("201 samples", body(cid, samples=[{"t": 1, "dir": "in"}] * 201)),
           ("101 events", body(cid, events=[{"t": 1, "k": "path", "d": ""}] * 101)),
           ("sample dir", body(cid, samples=[dict(body(cid)["samples"][0], dir="both")])),
           ("sample rid", body(cid, samples=[dict(body(cid)["samples"][0], rid="x")])),
           ("sample fps negativ", body(cid, samples=[dict(body(cid)["samples"][0], fps=-1)])),
           ("sample kbps Text", body(cid, samples=[dict(body(cid)["samples"][0], kbps="800")])),
           ("sample t fehlt", body(cid, samples=[{"dir": "in"}])),
           ("sample qlr", body(cid, samples=[dict(body(cid)["samples"][0], qlr="thermal")])),
           ("sample src zu lang", body(cid, samples=[dict(body(cid)["samples"][0], src="a" * 17)])),
           ("sample src mit Name", body(cid, samples=[dict(body(cid)["samples"][0], src="Anna Diagnose")])),
           ("event k", body(cid, events=[{"t": 1, "k": "chat", "d": ""}])),
           ("event d > 120", body(cid, events=[{"t": 1, "k": "path", "d": "x" * 121}])),
           ("event t Bool", body(cid, events=[{"t": True, "k": "path", "d": ""}]))]
    codes = {n: req("POST", "/api/calls/diag", A.k, b)[0] for n, b in bad}
    s, _ = req("POST", "/api/calls/diag", A.k, body(cid, samples=[{"t": 1, "dir": "in"}] * 200, events=[{"t": 1, "k": "path", "d": "x"}] * 100))
    ok(f"Grenzen genau: 200 samples / 100 events -> angenommen ({s})", s == 200)
    time.sleep(RATE_S_WAIT)
    n0 = len(logtxt() or "")
    ok(f"falsche Felder -> 400 ({[n for n, c in codes.items() if c != 400]})", all(c == 400 for c in codes.values()))
    s, d = req("POST", "/api/calls/diag", A.k, {"raw": "x" * (33 * 1024)})
    ok(f"Body > 32 KB -> 413 ({s})", s == 413)
    ok("abgelehnte POSTs schreiben keine Log-Zeile", logtxt() is None or "calldiag" not in (logtxt() or "")[n0:])

    # --- Annehmen, Log-Zeile ----------------------------------------------------------------------------------------
    extra = body(cid, secret="geheim-feld-xyz")
    extra["samples"][0].update(name="Anna Diagnose", ip="203.0.113.9")
    s, d = req("POST", "/api/calls/diag", A.k, extra)
    ok(f"Teilnehmerin A: 200 ok, unbekannte Felder verworfen ({s}, {d})", s == 200 and d.get("ok") is True)
    s2, _ = req("POST", "/api/calls/diag", A.k, body(cid))
    ok(f"zweiter POST innerhalb 15 s -> 429 ({s2})", s2 == 429)
    s3, _ = req("POST", "/api/calls/diag", B.k, body(cid, plat="ios", path="p2p", transport="?", node=""))
    ok(f"anderes Konto im selben Anruf: nicht betroffen (200) ({s3})", s3 == 200)
    txt = logtxt()
    if txt is None:
        ok(f"Server-Log {LOG} lesbar (Server mit Ausgabe in STATE_DIR/server.log starten)", False)
    else:
        time.sleep(0.3)
        lines = [ln for ln in (logtxt() or "")[n0:].splitlines() if ln.startswith("calldiag ")]
        ok(f"je angenommenem POST genau eine Zeile 'calldiag' ({len(lines)})", len(lines) == 2)
        recs = [json.loads(ln[len("calldiag "):]) for ln in lines]
        a = recs[0] if recs else {}
        ok(f"Zeile: Konto als Hash (10 Hex), kein Benutzername/Anzeigename ({a.get('acct')})",
           re.fullmatch(r"[0-9a-f]{10}", a.get("acct") or "") and not [ln for ln in lines if re.search(r"ca-dg|Diagnose", ln)])
        ok("Zeile: keine IP-Adressen, keine unbekannten Felder", not [ln for ln in lines if re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", ln)]
           and not [ln for ln in lines if "geheim-feld" in ln or '"ip"' in ln or '"name"' in ln])
        ok(f"Zeile: Anruf gekuerzt, Plattform, Weg, Knoten, Zaehler ({a.get('call')}, {a.get('n')})",
           a.get("call") == cid[:12] and a.get("plat") == "android" and a.get("path") == "horn" and a.get("node") == "horn-1.example.org"
           and a.get("n") == [2, 2])
        ok("Zeile: Proben und Ereignisse vollstaendig (rid, qlr, enc, kbps)",
           a.get("samples", [{}, {}])[1].get("rid") == "h" and a["samples"][1].get("qlr") == "bandwidth"
           and a["samples"][1].get("enc", "").startswith("MediaCodec") and a["samples"][0].get("kbps") == 812.5
           and a.get("events", [{}])[0].get("k") == "layers")
        ok("zwei Konten -> zwei verschiedene Hashes", len(recs) == 2 and recs[0]["acct"] != recs[1]["acct"])

    # --- Anrufende: auch nach dem Auflegen darf die letzte Meldung kommen ----------------------------------------------
    req("POST", f"/api/calls/{cid}/end", A.k, {"reason": "hangup"})
    cid2, s, d = A_.call(B, A, ab)
    s, _ = req("POST", "/api/calls/diag", B.k, body(cid2))
    ok(f"anderer Anruf: eigene Ratenbegrenzung (200) ({s})", s == 200)
    s, _ = req("POST", "/api/calls/diag", Cc.k, body(cid2))
    ok(f"Unbeteiligte auch im zweiten Anruf: 404 ({s})", s == 404)
    req("POST", f"/api/calls/{cid2}/end", B.k, {"reason": "hangup"})

    # --- Admin-Schalter ---------------------------------------------------------------------------------------------
    s, html = A_.admin("/calls")
    ok(f"Admin-Oberflaeche: Schalter calls_diag, an ({s})", s == 200 and re.search(r'name="calls_diag"[^>]*checked', html or ""))
    import admin as AD
    cur = auth.settings(fresh=True).get("calls") or {}
    AD._calls_save({"calls_enabled": "1", "calls_stun": cur.get("stun") or "", "calls_ring_s": "45"})
    ok("Admin speichert ohne Haken: calls.diag aus", auth.settings(fresh=True)["calls"].get("diag") is False)
    time.sleep(A_.WAIT)
    ok(f"aus: Feature calldiag1 fehlt in /api/version ({feats()})", "calldiag1" not in feats())
    time.sleep(RATE_PAUSE)
    s, _ = req("POST", "/api/calls/diag", A.k, body(cid))
    ok(f"aus: POST -> 404 ({s})", s == 404)
    AD._calls_save({"calls_enabled": "1", "calls_stun": cur.get("stun") or "", "calls_ring_s": "45", "calls_diag": "1"})
    ok("Admin speichert mit Haken: calls.diag an", auth.settings(fresh=True)["calls"].get("diag") is True)
    time.sleep(A_.WAIT)
    ok("wieder an: Feature calldiag1 zurueck", "calldiag1" in feats())


RATE_S_WAIT = 15.2                                     # nach dem Grenzwert-POST: Ratenbegrenzung abwarten
RATE_PAUSE = 4.0                                       # mit den WAIT-Pausen > 15 s seit dem letzten POST von A


if __name__ == "__main__":
    main()
