"""MCP mit OAuth-Anmeldung (2.6, Bugs-DB 2264, saga/oauth.py) gegen einen laufenden Test-Server (HK_TEST_BASE).

Voller Ablauf: Metadaten (RFC 9728/8414, beide Pfade, BASE_URL statt Host der Anfrage) -> 401 an /mcp mit resource_metadata
-> Registrierung (RFC 7591) -> Autorisierung mit PKCE S256 (ohne Sitzung ueber die echte Anmeldung zurueck zur Zustimmung)
-> Zustimmung (Teilmenge der Rechte) -> Token -> /mcp initialize, tools/list (genau die Werkzeuge der Bereiche), Aufruf ->
Refresh mit Rotation -> Wiederverwendung sperrt die Kette -> Widerruf (RFC 7009) -> "Verbundene Apps" (Liste, Trennen).
Fehlerfaelle: falscher Verifier, fremde redirect_uri, fehlende Zustimmung (Ablehnen, ohne Bereich, ohne CSRF-Token, fremder
Origin), abgelaufener Code, Code doppelt (sperrt auch das schon ausgegebene Token), Scope tel/tel_out -> invalid_scope, ohne
PKCE / plain, fremde resource, unbekannter Client, offene Weiterleitung, Registrierungs-Grenzen (Adressen, Name, Rate,
unbenutzte Clients verfallen), vertraulicher Client (Geheimnis Pflicht, post und basic), Admin-Schalter aus/an, "ueberall
abmelden", sga_-Tokens unveraendert (auch nicht unter den Apps, OAuth-Token nicht unter den API-Tokens).
RT-42: OAuth-Zugriffstoken gegen /api/ai/jobs/* (nie 2xx), /api/v1, Webhooks, Sitzungs-API, Chats; Chat-Werkzeuge
(Registry mcp=False) weder in tools/list noch aufrufbar; Attrappe mcp=False mit OAuth-Tok im Prozess -> 404.
Wegwerf-Konto oa-*, raeumt auf. Server-Umgebung: SAGA_OAUTH_REG_PER_IP=40 (Rate-Grenze wird mit 40 geprueft).
Aufruf (saga-test): PYTHONPATH=tests/pgshim:. python tests/mcp_oauth.py"""
import base64
import hashlib
import http.client
import json
import os
import re
import secrets
import sys
import time
import urllib.parse

import db

db.wait_ready()
import auth  # noqa: E402
from itsdangerous import TimestampSigner  # noqa: E402
from saga import apitok, oauth, tools  # noqa: E402
from saga.api_v1 import Fail  # noqa: E402
from saga.userdata import delete_user_data, revoke_user_logins  # noqa: E402

B = os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8080")
BASE = os.environ["BASE_URL"].rstrip("/")
HOST = urllib.parse.urlparse(BASE).netloc
SIGN = TimestampSigner(os.environ["SESSION_SECRET"])
U, PW = "oa-test-a", "Oa-Pass-" + secrets.token_hex(6)
U2 = "oa-test-b"
RU = "http://127.0.0.1:9/cb"                 # Loopback-Client (RFC 8252), wird nie aufgerufen
RU2 = "https://client.example/cb?x=1"
res = []
T0 = time.time()


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, "" if c or info == "" else f"-> {str(info)[:400]}", flush=True)


class Jar:
    """Sitzungs-Cookie "saga" mitfuehren (Set-Cookie der Antworten)."""

    def __init__(self, cookie=None):
        self.c = cookie

    def take(self, hs):
        for k, v in hs:
            if k.lower() == "set-cookie" and v.startswith("saga="):
                val = v.split(";", 1)[0][5:]
                self.c = val or None


def req(m, path, body=None, headers=None, jar=None, form=None, bearer=None):
    u = urllib.parse.urlparse(B)
    cn = http.client.HTTPConnection(u.hostname, u.port, timeout=20)
    h = {"Host": HOST}
    if jar and jar.c:
        h["Cookie"] = "saga=" + jar.c
    if bearer:
        h["Authorization"] = "Bearer " + bearer
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form, doseq=True).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    cn.request(m, path, body=data, headers=h)
    r = cn.getresponse()
    raw = r.read()
    hs = r.getheaders()
    if jar is not None:
        jar.take(hs)
    cn.close()
    try:
        d = json.loads(raw) if raw[:1] in (b"{", b"[") else {}
    except ValueError:
        d = {}
    return r.status, d, raw.decode(errors="replace"), {k.lower(): v for k, v in hs}


def session(uid, csrf):
    """Sitzung wie nach der Anmeldung (pwc = Passwort-Stand des Kontos, sonst verwirft _session_user sie)."""
    pwc = (auth.user_info(uid, fresh=True) or {}).get("epoch") or 0
    return SIGN.sign(base64.b64encode(json.dumps({"uid": uid, "csrf": csrf, "pwc": pwc, "sec_at": int(time.time())}).encode())).decode()


def pkce():
    v = secrets.token_urlsafe(48)
    return v, base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()


def register(**kw):
    b = {"client_name": "Testclient", "redirect_uris": [RU], "token_endpoint_auth_method": "none",
         "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]}
    b.update(kw)
    b = {k: v for k, v in b.items() if v is not None}
    return req("POST", "/oauth/register", b)


def authorize_url(cid, ch, ru=RU, state="st-1", scope=None, resource=BASE + "/mcp", method="S256", rt="code", extra=None):
    q = {"response_type": rt, "client_id": cid, "redirect_uri": ru, "state": state, "code_challenge": ch,
         "code_challenge_method": method, "resource": resource}
    if scope is not None:
        q["scope"] = scope
    q = {k: v for k, v in q.items() if v is not None}
    q.update(extra or {})
    return "/oauth/authorize?" + urllib.parse.urlencode(q)


def loc_q(hd):
    loc = hd.get("location") or ""
    return loc, dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(loc).query))


CSRF = secrets.token_urlsafe(16)
JAR = None


def consent(cid, ch, scopes=None, action="allow", ru=RU, state="st-1", scope=None, origin=None, csrf=None):
    """Autorisierung bis zur Zustimmung -> (status, query der Rueckleitung, html der Zustimmung)."""
    st, _, raw, hd = req("GET", authorize_url(cid, ch, ru=ru, state=state, scope=scope), jar=JAR)
    if st != 303 or not hd.get("location", "").startswith("/oauth/authorize?r="):
        return st, loc_q(hd)[1], raw
    rid = hd["location"].split("r=", 1)[1]
    st, _, page, _ = req("GET", hd["location"], jar=JAR)
    have = re.findall(r'name="scope" value="([^"]+)" checked', page)
    f = {"csrf": CSRF if csrf is None else csrf, "r": rid, "action": action, "scope": ["mcp"] + (have if scopes is None else scopes)}
    st, _, raw, hd = req("POST", "/oauth/authorize", form=f, jar=JAR, headers={"Origin": origin or BASE, "Sec-Fetch-Site": "same-origin"})
    return st, loc_q(hd)[1], page


def token(f, headers=None):
    return req("POST", "/oauth/token", form=f, headers=headers)


def code_for(cid, ver, ch, **kw):
    st, q, _ = consent(cid, ch, **kw)
    return q.get("code"), q


def exchange(cid, code, ver, ru=RU, **kw):
    f = {"grant_type": "authorization_code", "client_id": cid, "code": code, "redirect_uri": ru, "code_verifier": ver,
         "resource": BASE + "/mcp"}
    f.update(kw)
    return token({k: v for k, v in f.items() if v is not None})


N = [0]


def rpc(tok, method, params=None):
    N[0] += 1
    return req("POST", "/mcp", {"jsonrpc": "2.0", "id": N[0], "method": method, "params": params or {}}, bearer=tok,
               headers={"MCP-Protocol-Version": "2025-06-18", "Accept": "application/json, text/event-stream"})


def mcp_ok(tok):
    st, d, _, _ = rpc(tok, "tools/list")
    return st == 200 and bool((d.get("result") or {}).get("tools"))


def settings_api(**kw):
    cur = dict(auth.settings(fresh=True).get("api") or {})
    cur.update(kw)
    auth.save_setting("api", cur)
    time.sleep(5.5)                           # Server liest die Einstellungen hoechstens alle paar Sekunden neu


def cleanup():
    for u in (U, U2):
        if auth.get_user(u):
            delete_user_data(u, drop_user=True)
    with db.connect() as c, c:
        c.execute("DELETE FROM oauth_client WHERE name LIKE 'Testclient%' OR name LIKE 'oa-%' OR name LIKE 'Alt %'")


def part_meta():
    for p in ("/.well-known/oauth-protected-resource/mcp", "/.well-known/oauth-protected-resource"):
        st, d, _, hd = req("GET", p)
        ok(f"PRM {p}: resource {BASE}/mcp, AS {BASE}", st == 200 and d.get("resource") == BASE + "/mcp"
           and d.get("authorization_servers") == [BASE] and d.get("bearer_methods_supported") == ["header"], d)
    st, d, _, _ = req("GET", "/.well-known/oauth-authorization-server", headers={"Host": "anders.example", "X-Forwarded-Host": "x.example"})
    ok("AS-Metadaten: issuer/Endpunkte aus BASE_URL (nie aus Host/X-Forwarded-Host)", st == 200 and d.get("issuer") == BASE
       and d.get("token_endpoint") == BASE + "/oauth/token" and d.get("registration_endpoint") == BASE + "/oauth/register"
       and d.get("authorization_endpoint") == BASE + "/oauth/authorize" and d.get("revocation_endpoint") == BASE + "/oauth/revoke", d)
    ok("AS-Metadaten: nur S256, nur code, iss-Parameter", d.get("code_challenge_methods_supported") == ["S256"]
       and d.get("response_types_supported") == ["code"] and d.get("authorization_response_iss_parameter_supported") is True, d)
    sc = set(d.get("scopes_supported") or [])
    ok("Scopes ohne tel* und ohne webhooks (Weiche B.1 #14)", sc and not any(s.startswith("tel") for s in sc) and "webhooks" not in sc
       and "mcp" in sc and sc <= set(apitok.SCOPES), sorted(sc))
    st, d, _, hd = req("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    wa = hd.get("www-authenticate") or ""
    ok("401 an /mcp nennt resource_metadata (RFC 9728)", st == 401 and f'resource_metadata="{BASE}/.well-known/oauth-protected-resource/mcp"' in wa, wa)
    st, d, _, hd = req("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, bearer="sgo_" + "x" * 43)
    ok("Unbekanntes Zugriffstoken -> 401 invalid_token + resource_metadata", st == 401 and 'error="invalid_token"' in (hd.get("www-authenticate") or "")
       and "resource_metadata=" in (hd.get("www-authenticate") or ""), hd.get("www-authenticate"))


def part_register():
    bad = {"http fremd": ["http://evil.example/cb"], "javascript": ["javascript:alert(1)"], "Fragment": ["https://a.example/cb#x"],
           "Zugangsdaten": ["https://u:p@a.example/cb"], "leer": [], "zu viele": [f"https://a.example/{i}" for i in range(6)],
           "eigenes Schema": ["myapp://cb"], "kein Host": ["https:///cb"]}
    got = {k: register(redirect_uris=v)[0] for k, v in bad.items()}
    ok(f"Registrierung: ungueltige redirect_uris -> 400 ({got})", set(got.values()) == {400}, got)
    st, d, _, _ = register(client_name="oa-" + "x" * 250)
    ok("Registrierung: Name > 200 Zeichen -> 400", st == 400 and d.get("error") == "invalid_client_metadata", d)
    st, d, _, _ = register(client_name="Testclient " + "y" * 120)
    ok("Registrierung: langer Name auf 80 Zeichen gekuerzt", st == 201 and len(d.get("client_name") or "") == oauth.NAME_MAX, d)
    st, d, _, _ = register(grant_types=["client_credentials"])
    ok("Registrierung: fremder grant_type -> 400", st == 400, d)
    st, d, _, _ = register(scope="mcp tel")
    ok("Registrierung: scope mit tel -> 400", st == 400, d)
    st, d, _, hd = register(redirect_uris=[RU, "http://localhost:33418/cb", "http://[::1]:8/cb"])
    ok("Registrierung oeffentlicher Client (none, Loopback) -> 201 ohne Geheimnis", st == 201 and d.get("client_id", "").startswith("sgc_")
       and "client_secret" not in d and d.get("token_endpoint_auth_method") == "none" and hd.get("cache-control") == "no-store", d)
    with db.connect() as c:
        r = c.execute("SELECT secret_hash FROM oauth_client WHERE id=?", (d["client_id"],)).fetchone()
    ok("Client gespeichert (ohne Geheimnis)", r is not None and r[0] is None)
    return d["client_id"]


def part_limits():
    # unbenutzte Clients verfallen
    old = "sgc_" + secrets.token_urlsafe(18)
    with db.connect() as c, c:
        c.execute("INSERT INTO oauth_client (id, name, redirect_uris, created) VALUES (?,?,?,?)", (old, "Alt unbenutzt", json.dumps([RU]), time.time() - 2 * 86400))
    oauth.gc(force=True)
    with db.connect() as c:
        gone = c.execute("SELECT 1 FROM oauth_client WHERE id=?", (old,)).fetchone() is None
    ok("Unbenutzter Client nach 24 h entfernt", gone)
    # Rate je IP (Server: SAGA_OAUTH_REG_PER_IP)
    lim = int(os.environ.get("SAGA_OAUTH_REG_PER_IP") or 40)
    sts = [register(client_name=f"Testclient rate {i}")[0] for i in range(lim + 2)]
    ok(f"Registrierungs-Rate: hoechstens {lim} je Stunde und IP, dann 429", 429 in sts and sts.count(201) <= lim, sts[-5:])


def part_flow(cid):
    """Voller Ablauf mit echter Anmeldung (Formular) -> Zustimmung -> Token -> /mcp."""
    global JAR, CSRF
    ver, ch = pkce()
    jar = Jar()
    st, _, _, hd = req("GET", authorize_url(cid, ch, state="erst"), jar=jar)
    loc = hd.get("location") or ""
    ok("Ohne Sitzung -> Anmeldung mit Ruecksprung zur Zustimmung", st == 303 and loc.startswith("/login?next=%2Foauth%2Fauthorize%3Fr%3D"), loc)
    st, _, page, _ = req("GET", loc, jar=jar)
    m = re.search(r'name="csrf" value="([^"]+)"', page)
    st, _, _, hd = req("POST", "/login", form={"csrf": m.group(1) if m else "", "username": U, "password": PW}, jar=jar)
    nxt = hd.get("location") or ""
    ok("Nach der Anmeldung zurueck zur Zustimmung", st == 303 and re.fullmatch(r"/oauth/authorize\?r=[\w-]+", nxt), (st, nxt))
    st, _, page, hd = req("GET", nxt, jar=jar)
    csp = hd.get("content-security-policy") or ""
    ok("Zustimmungsseite: Client, Server-Adresse, Ruecksprung-Host, Rechte", st == 200 and "Testclient" in page and BASE in page
       and "127.0.0.1" in page and 'value="read:pages"' in page and 'value="write:pages"' in page, page[:300])
    ok("Zustimmungsseite: Clickjacking-Schutz (frame-ancestors 'none', X-Frame-Options DENY)", "frame-ancestors 'none'" in csp
       and hd.get("x-frame-options") == "DENY", (csp, hd.get("x-frame-options")))
    ok("Zustimmungsseite: form-action nur self + Ruecksprung-Origin", "form-action 'self' http://127.0.0.1:9" in csp, csp)
    ok("Zustimmungsseite: kein Scope tel und keine Webhooks angeboten", "tel" not in re.findall(r'name="scope" value="([^"]+)"', page)
       and 'value="webhooks"' not in page)
    rid = nxt.split("r=", 1)[1]
    csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    have = re.findall(r'name="scope" value="([^"]+)" checked', page)
    pick = [s for s in have if s not in ("write:pages", "write:db", "write:files")]       # Person nimmt Schreibrechte teils weg
    st, _, _, hd = req("POST", "/oauth/authorize", form={"csrf": csrf, "r": rid, "action": "allow", "scope": ["mcp"] + pick},
                       jar=jar, headers={"Origin": BASE, "Sec-Fetch-Site": "same-origin"})
    loc, q = loc_q(hd)
    ok("Erlauben -> Code an redirect_uri mit state und iss", st == 303 and loc.startswith(RU + "?") and q.get("code", "").startswith("sgk_")
       and q.get("state") == "erst" and q.get("iss") == BASE, loc)
    st, d, _, hd = exchange(cid, q["code"], ver)
    ok("Token: Zugriff + Refresh, Bearer, kurzlebig, no-store", st == 200 and d.get("access_token", "").startswith("sgo_")
       and d.get("refresh_token", "").startswith("sgr_") and d.get("token_type") == "Bearer" and 0 < d.get("expires_in", 0) <= 3600
       and hd.get("cache-control") == "no-store", d)
    granted = d.get("scope", "").split()
    ok("Token: Bereiche = Auswahl der Person (+mcp), ohne write:pages, nie tel", "mcp" in granted and "read:pages" in granted
       and "write:pages" not in granted and "write:tasks" in granted and not any(s.startswith("tel") for s in granted), granted)
    with db.connect() as c:
        rows = c.execute("SELECT hash, prefix FROM api_token WHERE grant_id IS NOT NULL AND uid=?", (U,)).fetchall()
        refs = c.execute("SELECT hash FROM oauth_refresh r JOIN oauth_grant g ON g.id=r.grant_id WHERE g.uid=?", (U,)).fetchall()
        allv = json.dumps(c.execute("SELECT * FROM api_token WHERE uid=?", (U,)).fetchall() + c.execute(
            "SELECT * FROM oauth_refresh").fetchall() + c.execute("SELECT * FROM oauth_code").fetchall(), default=str)
    ok("Token nur gehasht gespeichert (SHA-256, kein Klartext in api_token/oauth_refresh/oauth_code)",
       rows and refs and d["access_token"] not in allv and d["refresh_token"] not in allv and q["code"] not in allv
       and any(r[0] == hashlib.sha256(d["access_token"].encode()).hexdigest() for r in rows))
    acc = d["access_token"]
    st, i, _, _ = rpc(acc, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    ok("/mcp initialize mit OAuth-Token", st == 200 and (i.get("result") or {}).get("serverInfo", {}).get("name") == "saga", i)
    st, l, _, _ = rpc(acc, "tools/list")
    names = [x["name"] for x in (l.get("result") or {}).get("tools") or []]
    want = [x["name"] for x in tools.mcp_listing(apitok.Tok(0, U, "x", "x", granted, None, None))]
    ok(f"tools/list = Werkzeuge genau dieser Bereiche ({len(names)})", names == want and "search_pages" in names
       and "create_page" not in names and "create_task" in names, (names, want))
    st, r, _, _ = rpc(acc, "tools/call", {"name": "create_task", "arguments": {"text": "oa-Aufgabe per OAuth"}})
    ok("tools/call create_task (write:tasks erlaubt)", st == 200 and (r.get("result") or {}).get("isError") is False, r)
    st, r, _, _ = rpc(acc, "tools/call", {"name": "create_page", "arguments": {"title": "oa-darf nicht"}})
    ok("tools/call create_page ohne write:pages -> Unknown tool", st == 200 and (r.get("error") or {}).get("code") == -32602, r)
    st, r, _, _ = rpc(acc, "tools/call", {"name": "search_pages", "arguments": {"query": "oa"}})
    ok("tools/call search_pages", st == 200 and (r.get("result") or {}).get("isError") is False, r)
    JAR, CSRF = jar, csrf                    # weitere Zustimmungen in derselben (echten) Sitzung
    return acc, d["refresh_token"], granted


def part_errors(cid):
    ver, ch = pkce()
    # unbekannter Client / fremde redirect_uri: Fehlerseite, nie eine Umleitung (offene Weiterleitung)
    st, _, raw, hd = req("GET", authorize_url("sgc_" + "u" * 24, ch), jar=JAR)
    ok("Unbekannter Client -> Fehlerseite ohne Umleitung", st == 400 and "location" not in hd and "Verbindung nicht" in raw or st == 400 and "location" not in hd)
    st, _, raw, hd = req("GET", authorize_url(cid, ch, ru="https://evil.example/cb"), jar=JAR)
    ok("Fremde redirect_uri -> Fehlerseite ohne Umleitung", st == 400 and "location" not in hd, (st, hd.get("location")))
    st, _, raw, hd = req("GET", authorize_url(cid, ch, ru=RU + "/x"), jar=JAR)
    ok("redirect_uri nur exakt (Pfad angehaengt) -> Fehlerseite", st == 400 and "location" not in hd)
    for name, kw, err in (("ohne PKCE", {"ch": None}, "invalid_request"), ("PKCE plain", {"method": "plain"}, "invalid_request"),
                          ("fremde resource", {"resource": "https://evil.example/mcp"}, "invalid_target"),
                          ("resource ohne /mcp", {"resource": BASE}, "invalid_target"),
                          ("Scope tel", {"scope": "mcp read:pages tel"}, "invalid_scope"),
                          ("Scope tel_out", {"scope": "tel_out"}, "invalid_scope"),
                          ("Scope webhooks", {"scope": "webhooks"}, "invalid_scope"),
                          ("response_type token", {"rt": "token"}, "unsupported_response_type")):
        a = dict(kw)
        c = a.pop("ch", ch)
        st, _, _, hd = req("GET", authorize_url(cid, c, **a), jar=JAR)
        loc, q = loc_q(hd)
        ok(f"Autorisierung {name} -> {err} an redirect_uri (mit state, iss)", st == 303 and loc.startswith(RU) and q.get("error") == err
           and q.get("state") == "st-1" and q.get("iss") == BASE, (st, loc))
    st, _, _, hd = req("GET", authorize_url(cid, ch) + "&state=zwei", jar=JAR)
    ok("Doppelte Parameter -> Fehlerseite", st == 400 and "location" not in hd)
    # fehlende Zustimmung
    st, q, _ = consent(cid, ch, action="deny")
    ok("Ablehnen -> error=access_denied, kein Code", st == 303 and q.get("error") == "access_denied" and "code" not in q and q.get("state") == "st-1", q)
    st, q, page = consent(cid, ch, scopes=[])
    ok("Erlauben ohne Bereich -> keine Weiterleitung, Hinweis", st == 200 and not q, st)
    st, q, _ = consent(cid, ch, csrf="falsch")
    ok("Zustimmung ohne gueltiges CSRF-Token -> 403, kein Code", st == 403 and "code" not in q, st)
    st, q, _ = consent(cid, ch, origin="https://evil.example")
    ok("Zustimmung von fremdem Origin -> 403, kein Code", st == 403 and "code" not in q, st)
    st, _, _, hd = req("GET", authorize_url(cid, ch), jar=JAR)
    rid = (hd.get("location") or "").split("r=", 1)[-1]
    st, _, _, hd = req("POST", "/oauth/authorize", form={"csrf": CSRF, "r": rid, "action": "allow", "scope": ["mcp", "read:pages"]}, jar=JAR,
                       headers={"Origin": BASE, "Sec-Fetch-Site": "cross-site"})
    ok("Zustimmung mit Sec-Fetch-Site cross-site -> 403, kein Code", st == 403 and "location" not in hd, st)
    st, _, _, hd = req("POST", "/oauth/authorize", form={"csrf": CSRF, "r": rid, "action": "allow", "scope": ["mcp", "read:pages"]}, jar=JAR,
                       headers={"Origin": "null", "Sec-Fetch-Site": "same-origin"})
    ok("Zustimmung mit Origin null (Chrome bei no-referrer), same-origin + CSRF -> Code", st == 303 and "code=" in (hd.get("location") or ""), st)
    st, _, _, hd = req("POST", "/oauth/authorize", form={"csrf": CSRF, "r": rid, "action": "allow", "scope": ["mcp", "read:pages"]}, jar=JAR,
                       headers={"Origin": BASE})
    ok("Dieselbe Anfrage ein zweites Mal zugestimmt -> Fehlerseite (einmalig)", st == 400 and "location" not in hd, st)
    st, _, raw, _ = req("POST", "/oauth/authorize", form={"csrf": CSRF, "r": "x" * 24, "action": "allow", "scope": ["mcp", "read:pages"]}, jar=JAR,
                        headers={"Origin": BASE})
    ok("Zustimmung mit unbekannter Anfrage -> Fehlerseite", st == 400, st)
    st, _, raw, _ = req("POST", "/oauth/authorize", form={"csrf": CSRF, "r": "x" * 24, "action": "allow"}, headers={"Origin": BASE})
    ok("Zustimmung ohne Sitzung -> 403", st == 403, st)
    # falscher Verifier -> Code verbraucht
    code, _ = code_for(cid, ver, ch)
    st, d, _, _ = exchange(cid, code, pkce()[0])
    ok("Token mit falschem Verifier -> invalid_grant", st == 400 and d.get("error") == "invalid_grant", d)
    st, d, _, _ = exchange(cid, code, ver)
    ok("Danach derselbe Code mit richtigem Verifier -> invalid_grant (verbraucht)", st == 400 and d.get("error") == "invalid_grant", d)
    code, _ = code_for(cid, ver, ch)
    st, d, _, _ = exchange(cid, code, None)
    ok("Token ohne Verifier -> invalid_grant", st == 400 and d.get("error") == "invalid_grant", d)
    # fremde redirect_uri beim Einloesen
    code, _ = code_for(cid, ver, ch)
    st, d, _, _ = exchange(cid, code, ver, ru="http://localhost:33418/cb")
    ok("Token mit anderer (registrierter) redirect_uri -> invalid_grant", st == 400 and d.get("error") == "invalid_grant", d)
    # abgelaufener Code
    code, _ = code_for(cid, ver, ch)
    with db.connect() as c, c:
        c.execute("UPDATE oauth_code SET expires=? WHERE hash=?", (time.time() - 1, hashlib.sha256(code.encode()).hexdigest()))
    st, d, _, _ = exchange(cid, code, ver)
    ok("Abgelaufener Code -> invalid_grant", st == 400 and d.get("error") == "invalid_grant", d)
    # fremde resource beim Einloesen
    code, _ = code_for(cid, ver, ch)
    st, d, _, _ = exchange(cid, code, ver, resource="https://evil.example/mcp")
    ok("Token mit fremder resource -> invalid_target", st == 400 and d.get("error") == "invalid_target", d)
    # Code doppelt: zweites Einloesen sperrt auch das schon ausgegebene Token
    code, _ = code_for(cid, ver, ch)
    st, d, _, _ = exchange(cid, code, ver)
    first = d.get("access_token")
    ok("Code einmal eingeloest -> Token gueltig", st == 200 and mcp_ok(first), d)
    st, d2, _, _ = exchange(cid, code, ver)
    ok("Code doppelt -> invalid_grant", st == 400 and d2.get("error") == "invalid_grant", d2)
    ok("Code doppelt -> schon ausgegebenes Zugriffstoken gesperrt", not mcp_ok(first))
    st, d3, _, _ = token({"grant_type": "refresh_token", "client_id": cid, "refresh_token": d.get("refresh_token", "")})
    ok("Code doppelt -> Refresh-Token der Verbindung gesperrt", st == 400 and d3.get("error") == "invalid_grant", d3)
    # Code eines anderen Clients
    st, o, _, _ = register(client_name="Testclient anderer")
    code, _ = code_for(cid, ver, ch)
    st, d, _, _ = exchange(o["client_id"], code, ver)
    ok("Code mit anderem client_id -> invalid_grant", st == 400 and d.get("error") == "invalid_grant", d)
    st, d, _, _ = token({"grant_type": "password", "client_id": cid, "username": U, "password": PW})
    ok("grant_type password -> unsupported_grant_type", st == 400 and d.get("error") == "unsupported_grant_type", d)
    st, d, _, _ = req("POST", "/oauth/token", {"grant_type": "authorization_code", "client_id": cid})
    ok("Token-Anfrage als JSON -> invalid_request", st == 400 and d.get("error") == "invalid_request", d)
    st, d, _, _ = token({"grant_type": "authorization_code", "client_id": "sgc_" + "z" * 24, "code": "sgk_x"})
    ok("Unbekannter Client am Token-Endpunkt -> 401 invalid_client", st == 401 and d.get("error") == "invalid_client", d)


def part_rt42(acc):
    """RT-42: OAuth-Zugriffstoken erreicht nie /api/ai/jobs/*, keinen Client-Schritt, kein Chat-Werkzeug und nichts ausser /mcp."""
    jobs = (("GET", "/api/ai/jobs", None), ("POST", "/api/ai/jobs", {"text": "x"}), ("GET", "/api/ai/jobs/1", None),
            ("PUT", "/api/ai/jobs/1/plan", {"plan_v": 1}), ("POST", "/api/ai/jobs/1/approve", {"plan_v": 1, "plan_hash": "x"}),
            ("POST", "/api/ai/jobs/1/act", {"action": "later"}), ("GET", "/api/ai/jobs/outbox", None),
            ("GET", "/api/ai/jobs/outbox?device=1", None), ("POST", "/api/ai/jobs/1/inbox", {"msg_id": 1}),
            ("GET", "/api/ai/jobs/inbox", None), ("POST", "/api/ai/jobs/1/cancel", {}), ("POST", "/api/ai/jobs/1/undo", {}))
    got = {}
    for m, p, b in jobs:
        for how in ("bearer", "x-api-key", "x-saga"):
            h = {"X-API-Key": acc} if how == "x-api-key" else ({"X-Saga": "1", "Authorization": "Bearer " + acc} if how == "x-saga" else None)
            st = req(m, p, b, bearer=acc if how == "bearer" else None, headers=h)[0]
            got[f"{m} {p} [{how}]"] = st
    two = {k: v for k, v in got.items() if 200 <= v < 300}
    ok(f"RT-42: /api/ai/jobs/* mit OAuth-Token nie 2xx ({len(got)} Anfragen, Codes {sorted(set(got.values()))})", not two, two)
    other = {}
    for m, p, b in (("GET", "/api/v1/me", None), ("GET", "/api/v1/pages?q=a", None), ("GET", "/api/v1/webhooks", None),
                    ("POST", "/api/v1/webhooks", {"url": "https://a.example/h", "events": ["page.created"]}),
                    ("GET", "/api/account/api-tokens", None), ("POST", "/api/account/api-tokens", {"name": "x", "scopes": ["mcp"]}),
                    ("GET", "/api/account/oauth-apps", None), ("GET", "/api/chats", None), ("GET", "/api/ai/status", None),
                    ("POST", "/api/ai/ask", {"q": "x"}), ("GET", "/api/account", None), ("GET", "/api/calls", None)):
        other[f"{m} {p}"] = req(m, p, b, bearer=acc, headers={"X-Saga": "1"})[0]
        other[f"{m} {p} [x-api-key]"] = req(m, p, b, headers={"X-API-Key": acc, "X-Saga": "1"})[0]
    two = {k: v for k, v in other.items() if 200 <= v < 300}
    ok(f"RT-42: OAuth-Token gilt nur fuer /mcp - /api/v1, Webhooks, Sitzungs-API, Chats, KI nie 2xx ({len(other)})", not two, two)
    st, d, _, _ = req("GET", "/api/v1/me", bearer=acc)
    ok("OAuth-Token an /api/v1 -> 401 invalid_token", st == 401 and d.get("code") == "invalid_token", (st, d))
    st, l, _, _ = rpc(acc, "tools/list")
    names = {x["name"] for x in (l.get("result") or {}).get("tools") or []}
    non_mcp = {x.name for x in tools.REGISTRY if not x.mcp}
    ok(f"RT-42: tools/list nur Registry-Werkzeuge mit mcp=True (keine Chat-Werkzeuge, {len(non_mcp)} mit mcp=False)",
       names and names <= {x.name for x in tools.for_mcp()} and not names & non_mcp, names & non_mcp)
    probe = sorted(non_mcp | {"send_chat", "chat_send", "send_message", "call", "approve_job", "job_approve", "outbox", "inbox"})
    bad = {}
    for n in probe:
        st, r, _, _ = rpc(acc, "tools/call", {"name": n, "arguments": {}})
        if (r.get("error") or {}).get("code") != -32602:
            bad[n] = r
    ok(f"RT-42: Chat-/Auftrags-Werkzeuge per tools/call -> Unknown tool ({len(probe)} Namen)", not bad, bad)
    # Attrappe mit mcp=False (wie spaeter der Chat-Schritt) im Prozess: mit einem OAuth-Tok nie aufrufbar
    dummy = tools.Tool("oa_chat_dummy", "write:pages", "Attrappe", {}, [], True, klass="out", undo="final", mcp=False, kollegin=True)
    tools.register(dummy)
    try:
        otok = apitok.Tok(10 ** 12, U, "oauth", "sgo_", list(oauth.SCOPES), None, time.time() + 60, grant_id=1)
        try:
            tools.call(otok, "oa_chat_dummy", {})
            r404 = False
        except Fail as f:
            r404 = f.status == 404
        ok("RT-42: Attrappe mcp=False mit OAuth-Tok (tools.call) -> 404", r404)
        ok("RT-42: Attrappe nicht in tools/list eines OAuth-Tok", "oa_chat_dummy" not in {x["name"] for x in tools.mcp_listing(otok)})
        from saga import mcp
        r = mcp.handle(otok, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "oa_chat_dummy", "arguments": {}}})
        ok("RT-42: Attrappe per mcp.handle mit OAuth-Tok -> Unknown tool", (r.get("error") or {}).get("code") == -32602, r)
    finally:
        tools.unregister("oa_chat_dummy")


def part_refresh(cid, ref):
    ver, ch = pkce()
    st, d, _, _ = token({"grant_type": "refresh_token", "client_id": cid, "refresh_token": ref})
    ok("Refresh -> neues Zugriffs- und Refresh-Token (Rotation)", st == 200 and d.get("refresh_token") not in (None, ref)
       and d.get("access_token", "").startswith("sgo_"), d)
    acc2, ref2 = d.get("access_token"), d.get("refresh_token")
    ok("Neues Zugriffstoken gilt an /mcp", mcp_ok(acc2))
    st, d, _, _ = token({"grant_type": "refresh_token", "client_id": cid, "refresh_token": ref2, "scope": "mcp read:pages write:pages"})
    ok("Refresh mit groesserem Scope -> invalid_scope", st == 400 and d.get("error") == "invalid_scope", d)
    st, d, _, _ = token({"grant_type": "refresh_token", "client_id": cid, "refresh_token": ref2, "scope": "mcp read:pages"})
    ok("Refresh mit kleinerem Scope -> nur dieser", st == 200 and set(d.get("scope", "").split()) == {"mcp", "read:pages"}, d)
    acc3, ref3 = d.get("access_token"), d.get("refresh_token")
    st, d, _, _ = token({"grant_type": "refresh_token", "client_id": cid, "refresh_token": ref})
    ok("Alter Refresh-Token noch einmal -> invalid_grant (Wiederverwendung)", st == 400 and d.get("error") == "invalid_grant", d)
    ok("Wiederverwendung sperrt die Kette: neuestes Zugriffstoken ungueltig", not mcp_ok(acc3) and not mcp_ok(acc2))
    st, d, _, _ = token({"grant_type": "refresh_token", "client_id": cid, "refresh_token": ref3})
    ok("Wiederverwendung sperrt die Kette: neuester Refresh-Token ungueltig", st == 400 and d.get("error") == "invalid_grant", d)
    with db.connect() as c:
        why = c.execute("SELECT why FROM oauth_grant WHERE uid=? AND why='refresh_reuse'", (U,)).fetchone()
    ok("Verbindung als 'refresh_reuse' gesperrt", why is not None)


def full_tokens(cid, **kw):
    ver, ch = pkce()
    code, _ = code_for(cid, ver, ch)
    st, d, _, _ = exchange(cid, code, ver, **kw)
    return d


def part_revoke(cid):
    old = full_tokens(cid)
    d = full_tokens(cid)
    ok("Neue Zustimmung derselben App ersetzt die alte Verbindung (altes Token ungueltig, neues gilt)",
       not mcp_ok(old["access_token"]) and mcp_ok(d["access_token"]))
    acc, ref = d["access_token"], d["refresh_token"]
    st, a, _, _ = req("GET", "/api/account/oauth-apps", jar=JAR, headers={"X-Saga": "1"})
    apps = a.get("apps") or []
    ok("Verbundene Apps: genau eine Zeile je App mit Name, Rechten, zuletzt benutzt", st == 200 and len([x for x in apps if x["name"] == "Testclient"]) == 1
       and "mcp" not in apps[0]["scopes"] and apps[0]["last_used"] and apps[0]["host"] == "127.0.0.1" and a.get("mcp_url") == BASE + "/mcp", a)
    st, t, _, _ = req("GET", "/api/account/api-tokens", jar=JAR, headers={"X-Saga": "1"})
    ok("API-Tokens-Liste zeigt keine OAuth-Zugriffstoken", st == 200 and not any(x["prefix"].startswith("sgo_") for x in t.get("tokens") or []), t)
    st, _, _, _ = req("POST", "/oauth/revoke", form={"token": ref, "client_id": cid})
    ok("Widerruf (RFC 7009) Refresh-Token -> 200", st == 200)
    ok("Nach Widerruf: Zugriffstoken ungueltig", not mcp_ok(acc))
    st, d2, _, _ = token({"grant_type": "refresh_token", "client_id": cid, "refresh_token": ref})
    ok("Nach Widerruf: Refresh -> invalid_grant", st == 400 and d2.get("error") == "invalid_grant", d2)
    st, _, _, _ = req("POST", "/oauth/revoke", form={"token": "sgr_unbekannt", "client_id": cid})
    ok("Widerruf unbekannter Token -> 200 (RFC 7009)", st == 200)
    # Widerruf nur des Zugriffstokens
    d = full_tokens(cid)
    req("POST", "/oauth/revoke", form={"token": d["access_token"], "client_id": cid})
    st, d2, _, _ = token({"grant_type": "refresh_token", "client_id": cid, "refresh_token": d["refresh_token"]})
    ok("Widerruf Zugriffstoken: dieses ungueltig, Refresh geht weiter", not mcp_ok(d["access_token"]) and st == 200, d2)
    # fremder Client darf nicht widerrufen
    st, o, _, _ = register(client_name="Testclient fremd")
    req("POST", "/oauth/revoke", form={"token": d2["refresh_token"], "client_id": o["client_id"]})
    ok("Widerruf durch fremden Client wirkt nicht", mcp_ok(d2["access_token"]))
    # Trennen in den Einstellungen
    st, a, _, _ = req("GET", "/api/account/oauth-apps", jar=JAR, headers={"X-Saga": "1"})
    gid = a["apps"][0]["id"]
    st, _, _, _ = req("DELETE", f"/api/account/oauth-apps/{gid}", bearer=d2["access_token"])
    ok("Trennen nur mit Sitzung (OAuth-Token -> 401)", st == 401)
    st, _, _, _ = req("DELETE", f"/api/account/oauth-apps/{gid}", jar=Jar(session(U2, "x")), headers={"X-Saga": "1"})
    ok("Trennen einer fremden Verbindung -> 404", st == 404)
    st, _, _, _ = req("DELETE", f"/api/account/oauth-apps/{gid}", jar=JAR, headers={"X-Saga": "1"})
    ok("Trennen -> 200, Zugriffstoken sofort ungueltig", st == 200 and not mcp_ok(d2["access_token"]))
    st, d3, _, _ = token({"grant_type": "refresh_token", "client_id": cid, "refresh_token": d2["refresh_token"]})
    ok("Getrennt: Refresh -> invalid_grant", st == 400 and d3.get("error") == "invalid_grant", d3)
    st, a, _, _ = req("GET", "/api/account/oauth-apps", jar=JAR, headers={"X-Saga": "1"})
    ok("Getrennt: nicht mehr unter Verbundene Apps", not a.get("apps"), a)


def part_confidential():
    st, c, _, _ = register(client_name="Testclient geheim", redirect_uris=[RU2], token_endpoint_auth_method="client_secret_post")
    ok("Vertraulicher Client -> Geheimnis einmal in der Antwort", st == 201 and c.get("client_secret", "").startswith("sgs_"), c)
    with db.connect() as cn:
        h = cn.execute("SELECT secret_hash FROM oauth_client WHERE id=?", (c["client_id"],)).fetchone()[0]
    ok("Client-Geheimnis nur gehasht", h == hashlib.sha256(c["client_secret"].encode()).hexdigest())
    ver, ch = pkce()
    code, q = code_for(c["client_id"], ver, ch, ru=RU2)
    ok("Rueckleitung haengt an vorhandene Abfrage an (?x=1&code=...)", code and q.get("x") == "1", q)
    st, d, _, _ = exchange(c["client_id"], code, ver, ru=RU2)
    ok("Vertraulicher Client ohne Geheimnis -> 401 invalid_client", st == 401 and d.get("error") == "invalid_client", d)
    code, _ = code_for(c["client_id"], ver, ch, ru=RU2)
    st, d, _, _ = exchange(c["client_id"], code, ver, ru=RU2, client_secret="sgs_falsch")
    ok("Falsches Geheimnis -> 401", st == 401, d)
    code, _ = code_for(c["client_id"], ver, ch, ru=RU2)
    st, d, _, _ = exchange(c["client_id"], code, ver, ru=RU2, client_secret=c["client_secret"])
    ok("client_secret_post -> Token", st == 200 and d.get("access_token"), d)
    basic = base64.b64encode(f"{c['client_id']}:{c['client_secret']}".encode()).decode()
    st, d2, _, _ = token({"grant_type": "refresh_token", "refresh_token": d["refresh_token"]}, headers={"Authorization": "Basic " + basic})
    ok("client_secret_basic -> Refresh", st == 200 and d2.get("access_token"), d2)
    return d2["access_token"], d2["refresh_token"], c


def part_switch(cid, sga):
    d = full_tokens(cid)
    acc, ref = d["access_token"], d["refresh_token"]
    settings_api(oauth=False)
    try:
        ok("Admin aus: Metadaten 404", req("GET", "/.well-known/oauth-authorization-server")[0] == 404
           and req("GET", "/.well-known/oauth-protected-resource/mcp")[0] == 404)
        ok("Admin aus: Registrierung 403", register(client_name="Testclient aus")[0] == 403)
        ok("Admin aus: OAuth-Token an /mcp ungueltig", not mcp_ok(acc))
        st, d2, _, _ = token({"grant_type": "refresh_token", "client_id": cid, "refresh_token": ref})
        ok("Admin aus: Refresh verweigert", st in (400, 403) and "access_token" not in d2, d2)
        ver, ch = pkce()
        st, _, raw, hd = req("GET", authorize_url(cid, ch), jar=JAR)
        ok("Admin aus: Autorisierung -> Hinweisseite", st == 403 and "location" not in hd, st)
        st, d3, _, hd = rpc(sga, "tools/list")
        ok("Admin aus: sga_-Token an /mcp weiter gueltig, 401 ohne resource_metadata", st == 200
           and "resource_metadata" not in (req("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"})[3].get("www-authenticate") or ""))
    finally:
        settings_api(oauth=True)
    ok("Admin wieder an: Zugriffstoken gilt wieder (Verbindung wurde nicht getrennt)", mcp_ok(acc))
    return acc, ref


def part_sga():
    st, d, _, _ = req("POST", "/api/account/api-tokens", {"name": "oa sga", "scopes": ["mcp", "read:pages"]}, jar=Jar(session(U, "x")),
                      headers={"X-Saga": "1"})
    sga = d.get("token", "")
    ok("sga_-Token anlegen wie bisher", st == 200 and sga.startswith("sga_"), d)
    ok("sga_-Token an /mcp", mcp_ok(sga))
    st, _, _, _ = req("GET", "/api/v1/me", bearer=sga)
    ok("sga_-Token an /api/v1 weiter gueltig", st == 200)
    st, _, _, _ = req("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers={"X-API-Key": sga})
    ok("sga_-Token im Ausweich-Header X-API-Key weiter gueltig", st == 200)
    return sga


def main():
    cleanup()
    prev = dict(auth.settings(fresh=True).get("api") or {})
    try:
        auth.create_user(U, PW, "Olga Auth")
        auth.create_user(U2, None, U2)
        settings_api(enabled=True, mcp=True, oauth=True, rate=3000, rate_write=1000, max_tokens=50)
        part_meta()
        cid = part_register()
        acc, ref, granted = part_flow(cid)
        part_rt42(acc)
        part_refresh(cid, ref)
        part_errors(cid)
        part_revoke(cid)
        cacc, cref, conf_c = part_confidential()
        sga = part_sga()
        a2, r2 = part_switch(cid, sga)
        st, d, _, _ = token({"grant_type": "refresh_token", "client_id": cid, "refresh_token": r2})
        ok("Refresh nach Admin-an", st == 200 and d.get("access_token"), d)
        revoke_user_logins(U)
        ok("Ueberall abmelden: Zugriffstoken ungueltig (oeffentlicher und vertraulicher Client)",
           not mcp_ok(d.get("access_token", "")) and not mcp_ok(cacc))
        st, d2, _, _ = token({"grant_type": "refresh_token", "client_id": cid, "refresh_token": d.get("refresh_token", "")})
        ok("Ueberall abmelden: Refresh -> invalid_grant", st == 400 and d2.get("error") == "invalid_grant", d2)
        st, d2, _, _ = token({"grant_type": "refresh_token", "client_id": conf_c["client_id"], "refresh_token": cref,
                              "client_secret": conf_c["client_secret"]})
        ok("Ueberall abmelden: Refresh des vertraulichen Clients -> invalid_grant", st == 400 and d2.get("error") == "invalid_grant", d2)
        ok("Ueberall abmelden: sga_-Token widerrufen (wie bisher)", not mcp_ok(sga))
        part_limits()
    finally:
        auth.save_setting("api", prev)
        cleanup()
    print(f"\nMCP-OAuth {sum(res)}/{len(res)} ({time.time() - T0:.0f} s)", "- ALLES GRUEN" if res and all(res) else "- FEHLER", flush=True)
    return 0 if res and all(res) else 1


if __name__ == "__main__":
    sys.exit(main())
