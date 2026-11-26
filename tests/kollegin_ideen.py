"""Kollegin K-D (2.6, Bugs-DB 2271): Tageszeile "Kurz gesagt" auf "Heute" (/api/ai/today, /api/app/today?line=1).

Teil Vertrag (S1):
- tests/today_vectors.json in sich stimmig: Format saga-today/v1, jede Antwort mit allen Feldern aus 'fields', line.text
  = parts verbunden, <= 3 Teile, <= 160 Zeichen, Ziele nur aus der festen Menge, v = Hash der Antwort, 'show' folgt aus der
  Antwort (Regeln 'display'), Widget-Teil ohne sentence/ask/info, RU-Plural-Tabelle vollstaendig.
- Server liefert dieselbe Form: Felder wie Fall 'normal', ETag = v und 304, ohne Faehigkeit ai {enabled:false}, Bearer
  (hk_/sga_) 403, ohne X-Saga 401; Ruhe (POST/DELETE quiet), 3x X -> genau eine Frage, PUT on/asked und 400 bei falschen
  Werten; /api/app/today ohne line=1 unveraendert, mit line=1 genau die Widget-Felder.
Wegwerf-Konten kd-*; stellt die KI-Einstellung wieder her.
Aufruf (saga-test): PYTHONPATH=tests/pgshim:. python tests/kollegin_ideen.py"""
import base64
import hashlib
import http.client
import json
import os
import secrets
import sys
import time
import urllib.parse

from itsdangerous import TimestampSigner

HERE = os.path.dirname(os.path.abspath(__file__))
VEC = json.load(open(os.path.join(HERE, "today_vectors.json"), encoding="utf-8"))
TARGETS = {"task": {"id"}, "row": {"id"}, "tasks": {"filter"}, "events": {"date"}, "jobs": {"group"}}
WIDGET = ("enabled", "on", "line", "waiting", "counts", "quiet", "v")
res = []


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, "" if c else str(info)[:500], flush=True)


# --- Vektoren (ohne Server) -----------------------------------------------------------------------------------------
def _v(d):
    d = {k: v for k, v in d.items() if k != "v"}
    return hashlib.sha256(json.dumps(d, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def _show(d):
    if not d.get("enabled"):
        return {"kurz": None, "pill": None, "ask": None, "waiting": None}
    ln = d.get("line")
    return {"kurz": {"label": d["label"], "parts": [p["text"] for p in ln["parts"]], "a11y": ln["a11y"]} if ln else None,
            "pill": {"text": d["quiet"]["text"], "undo": d["quiet"]["undo"]} if d.get("quiet") and not d.get("ask") else None,
            "ask": d.get("ask"), "waiting": d["waiting"]["text"] if d.get("waiting") else None}


def _line_ok(ln):
    if ln is None:
        return True
    ps = ln.get("parts") or []
    return (set(ln) == {"text", "a11y", "parts"} and 1 <= len(ps) <= 3 and len(ln["text"]) <= 160
            and ln["text"] == " · ".join(p["text"] for p in ps)
            and all(p["kind"] in ("event", "over", "due", "tomorrow") and _target_ok(p["target"])
                    and (("n" not in p) or (p["n"] > 1 and p["text"].endswith(f"(+{p['n'] - 1})"))) for p in ps))


def _target_ok(tg):
    return isinstance(tg, dict) and tg.get("type") in TARGETS and set(tg) == {"type"} | TARGETS[tg["type"]]


def vectors():
    ok("Vektoren: Format saga-today/v1 mit Kopf (note, fields, display, live)",
       VEC.get("format") == "saga-today/v1" and all(VEC.get(k) for k in ("note", "fields", "display", "live", "cases")))
    keys = {k for k in VEC["fields"] if "." not in k and k not in ("target", "a11y")} | {"enabled", "v"}
    ids = [c["id"] for c in VEC["cases"]]
    need = {"normal", "ruhig", "ruhe", "frage", "ohne_ai", "kalt", "sie", "th", "ru", "widget", "aus", "abend"}
    ok("Vektoren: alle verlangten Faelle da, ids eindeutig", need <= set(ids) and len(ids) == len(set(ids)), sorted(need - set(ids)))
    bad = []
    for c in VEC["cases"]:
        r = c["response"]
        if c["id"].startswith("widget"):
            part = (r or {}).get("line")
            if c["id"] == "widget_alt":
                if "line" in r:
                    bad.append(c["id"])
                continue
            if part.get("enabled") is False:
                if part != {"enabled": False}:
                    bad.append(c["id"])
                continue
            if set(part) != set(WIDGET) or not _line_ok(part["line"]):
                bad.append(c["id"])
            continue
        if c["status"] != 200 or r == {"enabled": False}:
            if c["show"] != _show({"enabled": False}) and c["id"] != "nicht_geaendert":
                bad.append(c["id"])
            continue
        if (set(r) != keys or r["v"] != _v(r) or not _line_ok(r["line"]) or c["show"] != _show(r)
                or (r["waiting"] and not _target_ok(r["waiting"]["target"]))
                or ((r["quiet"] or r["ask"] or not r["on"]) and r["line"] is not None)):
            bad.append(c["id"])
    ok("Vektoren: jede Antwort vollstaendig, v = Hash, Zeile/Ziele gueltig, 'show' folgt aus der Antwort", not bad, bad)
    th = next(c for c in VEC["cases"] if c["id"] == "th")["response"]
    ok("Vektoren: Thai-Teile ohne ค่ะ/ครับ, Satz mit Partikel",
       not any(x in p["text"] for p in th["line"]["parts"] for x in ("ค่ะ", "ครับ")) and "ค่ะ" in th["sentence"])
    ru = VEC["waiting_plural"]["ru"]["du"]
    ok("Vektoren: RU-Plural 1 / 2-4 / 5+ / 21", ru["1"].startswith("1 поручение") and ru["2"].startswith("2 поручения")
       and ru["5"].startswith("5 поручений") and ru["21"].startswith("21 поручение"))
    n = next(c for c in VEC["cases"] if c["id"] == "normal")
    ok("Vektoren: ETag = v, Live-Beispiel traegt v", n["headers"]["ETag"] == f'"{n["response"]["v"]}"'
       and VEC["live"]["data"]["v"] == n["response"]["v"])


# --- Server ---------------------------------------------------------------------------------------------------------
def server():
    import auth
    import db
    db.wait_ready()
    from db import connect
    from saga.core import _tok_hash

    B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8920"))
    HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
    S = TimestampSigner(os.environ["SESSION_SECRET"])
    A, OFF = "kd-anna", "kd-aus"

    def ck(u):
        return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()

    def req(m, p, u=None, body=None, hdr=None, xs=True):
        c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=30)
        h = {"Host": HOST, "Accept-Language": "de"}
        if u:
            h["Cookie"] = "saga=" + ck(u)
            if xs:
                h["X-Saga"] = "1"
        data = None
        if body is not None:
            h["Content-Type"] = "application/json"
            data = body if isinstance(body, str) else json.dumps(body)
        h.update(hdr or {})
        c.request(m, p, body=data, headers=h)
        r = c.getresponse()
        raw = r.read()
        hd = {k.lower(): v for k, v in r.getheaders()}
        try:
            out = json.loads(raw) if raw else None
        except ValueError:
            out = raw.decode(errors="replace")
        return r.status, out, hd

    def q(sql, args=()):
        with connect() as c, c:
            return c.execute(sql, args).fetchall()

    def cleanup():
        from saga.userdata import delete_user_data
        for u in [r[0] for r in q("SELECT username FROM users WHERE username LIKE 'kd-%'")]:
            delete_user_data(u)
        with connect() as c, c:
            for t_ in ("job", "user_kv", "task", "app_token"):
                c.execute(f"DELETE FROM {t_} WHERE uid LIKE 'kd-%'")
            c.execute("DELETE FROM users WHERE username LIKE 'kd-%'")

    prev = dict(auth.settings(fresh=True).get("ai") or {})
    cleanup()
    try:
        for u in (A, OFF):
            auth.create_user(u, "Kd-Pass-12345", u)
        auth.set_user_groups(auth.get_user(A)["id"], ["kd-familie"])
        cur = dict(prev)
        cur.update(enabled=True, provider="openai", url="http://127.0.0.1:9/v1", model="fake", groups=["kd-familie"])
        auth.save_setting("ai", cur)
        auth.settings(fresh=True)
        time.sleep(5.6)                                   # Einstellungen sind im Server 5 s zwischengespeichert
        today = time.strftime("%Y-%m-%d")
        q("INSERT INTO task (uid, text, due, prio, done, created) VALUES (?, ?, ?, 1, 0, ?)", (A, "Pavillon abholen", "2020-01-01", time.time()))
        q("INSERT INTO task (uid, text, due, prio, done, created) VALUES (?, ?, ?, 2, 0, ?)", (A, "Kuchen backen", today, time.time()))
        tok = "hk_" + secrets.token_urlsafe(24)
        q("INSERT INTO app_token (uid, name, hash, prefix, created) VALUES (?, 'kd', ?, 'hk_', ?)", (A, _tok_hash(tok), time.time()))
        want = set(next(c for c in VEC["cases"] if c["id"] == "normal")["response"])

        st, d, h = req("GET", "/api/ai/today?tz=Europe/Berlin", A)
        ok("GET: 200 mit genau den Feldern von Fall 'normal'", st == 200 and set(d) == want, (st, d))
        ok("GET: ETag = v, tz uebernommen, Zeile mit Aufgaben", h.get("etag") == f'"{d.get("v")}"' and d.get("tz") == "Europe/Berlin"
           and d.get("line") and [p["kind"] for p in d["line"]["parts"]] == ["over", "due"], (h.get("etag"), d))
        ok("GET: Zeile/Ziele in Vertragsform", _line_ok(d.get("line")), d.get("line"))
        st2, d2, _ = req("GET", "/api/ai/today?tz=Europe/Berlin", A, hdr={"If-None-Match": h.get("etag", "")})
        ok("GET: If-None-Match -> 304 ohne Inhalt", st2 == 304 and not d2, (st2, d2))
        st, d, _ = req("GET", "/api/ai/today?tz=Nirgendwo/Stadt", A)
        ok("GET: unbekannte Zone -> Zone des Kontos", st == 200 and d.get("tz") != "Nirgendwo/Stadt", d.get("tz"))
        st, d, _ = req("GET", "/api/ai/today", OFF)
        ok("Ohne Faehigkeit ai: 200 {enabled:false}", st == 200 and d == {"enabled": False}, (st, d))
        ok("Ohne Faehigkeit ai: Schreiben 403", req("POST", "/api/ai/today/quiet", OFF)[0] == 403
           and req("PUT", "/api/ai/today", OFF, {"on": False})[0] == 403)
        ok("Bearer hk_ an /api/ai/today -> 403", req("GET", "/api/ai/today", hdr={"Authorization": "Bearer " + tok})[0] == 403)
        ok("Bearer sga_ an /api/ai/today/quiet -> 403",
           req("POST", "/api/ai/today/quiet", hdr={"Authorization": "Bearer sga_" + "x" * 40})[0] == 403)
        ok("Ohne X-Saga -> 401", req("GET", "/api/ai/today", A, xs=False)[0] == 401)

        st, d, _ = req("POST", "/api/ai/today/quiet?tz=Europe/Berlin", A)
        ok("✕: Ruhe bis 05:00 Ortszeit, line null, keine Frage", st == 200 and bool(d.get("quiet")) and d["line"] is None
           and d["quiet"]["until"][10:] in ("T05:00:00+01:00", "T05:00:00+02:00") and d["ask"] is None, d)
        st, d, _ = req("GET", "/api/ai/today?tz=Europe/Berlin", A)
        ok("Ruhe gilt auch fuer den naechsten Abruf", d.get("quiet") and d["line"] is None, d)
        st, d, _ = req("DELETE", "/api/ai/today/quiet?tz=Europe/Berlin", A)
        ok("Wieder an: quiet null, Zeile wieder da", st == 200 and d["quiet"] is None and d["line"], d)
        req("POST", "/api/ai/today/quiet", A)
        st, d, _ = req("POST", "/api/ai/today/quiet", A)
        ok("Drittes ✕ in 14 Tagen: Frage", d.get("ask") and set(d["ask"]) == {"text", "yes", "no"} and d["quiet"], d)
        st, d, _ = req("PUT", "/api/ai/today", A, {"asked": True})
        ok("Frage 'Nein, passt': ask null, Ruhe bleibt", st == 200 and d["ask"] is None and d["quiet"], d)
        req("DELETE", "/api/ai/today/quiet", A)
        req("POST", "/api/ai/today/quiet", A)
        st, d, _ = req("POST", "/api/ai/today/quiet", A)
        ok("Danach nie wieder gefragt", d["ask"] is None and d["quiet"], d)
        st, d, _ = req("PUT", "/api/ai/today", A, {"on": False})
        ok("Aus: on false, line/quiet null", st == 200 and d["on"] is False and d["line"] is None and d["quiet"] is None, d)
        st, d, _ = req("PUT", "/api/ai/today", A, {"on": True})
        ok("Wieder ein: Zeile da, Ruhe aufgehoben", st == 200 and d["on"] and d["line"] and d["quiet"] is None, d)
        bad = [b for b in ({"on": "nein"}, {"foo": 1}, {}, {"asked": 1}, "[]", "kaputt")
               if req("PUT", "/api/ai/today", A, b)[0] != 400]
        kv = q("SELECT v FROM user_kv WHERE uid=? AND k='ai_today'", (A,))
        ok("PUT mit falschen Werten -> 400, nichts geschrieben", not bad and kv and not json.loads(kv[0][0]).get("off"), (bad, kv))

        hd = {"Authorization": "Bearer " + tok}
        st, w0, _ = req("GET", "/api/app/today", hdr=hd)
        st1, w1, _ = req("GET", "/api/app/today?line=1&tz=Europe/Berlin", hdr=hd)
        ok("Widget ohne line=1: kein Feld 'line'", st == 200 and "line" not in w0, w0)
        ok("Widget mit line=1: genau die Widget-Felder", st1 == 200 and set(w1.get("line") or {}) == set(WIDGET)
           and set(w1) - {"line"} == set(w0), w1)
        tok2 = "hk_" + secrets.token_urlsafe(24)
        q("INSERT INTO app_token (uid, name, hash, prefix, created) VALUES (?, 'kd', ?, 'hk_', ?)", (OFF, _tok_hash(tok2), time.time()))
        st, w2, _ = req("GET", "/api/app/today?line=1", hdr={"Authorization": "Bearer " + tok2})
        ok("Widget ohne Faehigkeit ai: line {enabled:false}", st == 200 and w2.get("line") == {"enabled": False}, w2)
    finally:
        auth.save_setting("ai", prev)
        auth.settings(fresh=True)
        cleanup()


if __name__ == "__main__":
    vectors()
    if "--nur-vektoren" not in sys.argv:
        server()
    print(f"\nKollegin Tageszeile {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)
