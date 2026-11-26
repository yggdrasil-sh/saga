"""Angriffsproben des Pruefers zu MCP mit OAuth (2.6, Bugs-DB 2264, saga/oauth.py) - ergaenzt tests/mcp_oauth.py.

redirect_uri exakt (Gross/Klein, Port, Schraegstrich, Fragment, IDN, localhost-Varianten), Registrierung (gefaehrliche
Adressen, CSP-Zeichen im Host), XSS ueber Client-Name/URIs auf Zustimmungs- und Fehlerseite, Rechteausweitung (scope,
resource, Formular, Refresh), Origin null / Sec-Fetch-Site, Wettlauf zweier gleichzeitiger Refresh- bzw. Code-Anfragen,
Gruppe verliert API-Recht, Admin-Widerruf, Konto loeschen, Metadaten mit fremdem Host. Wegwerf-Konten oa-*, raeumt auf.
Server: SAGA_OAUTH_REG_PER_IP>=40. Aufruf (saga-test): PYTHONPATH=tests/pgshim:. python tests/mcp_oauth_proben.py"""
import re
import sys
import threading
import time
import urllib.parse

import mcp_oauth as m  # noqa: E402  (db.wait_ready, Hilfen, Konten oa-test-*)
from mcp_oauth import BASE, Jar, auth, db, ok, req, session  # noqa: E402
from saga import apitok  # noqa: E402
from saga.userdata import delete_user_data  # noqa: E402

RU = "https://client.example/cb"


def reg(uris, name="Testclient Probe", method="none"):
    return m.register(client_name=name, redirect_uris=uris, token_endpoint_auth_method=method)


def jar_for(uid):
    return Jar(session(uid, m.CSRF))


def start(cid, ru, jar, **kw):
    ver, ch = m.pkce()
    st, _, raw, hd = req("GET", m.authorize_url(cid, ch, ru=ru, **kw), jar=jar)
    return ver, ch, st, raw, hd


def grant(cid, ru, jar, scopes=None):
    """Bis zum Token: -> (token-antwort, verifier)."""
    m.JAR = jar
    ver, ch = m.pkce()
    st, q, page = m.consent(cid, ch, ru=ru, scopes=scopes)
    st, d, _, _ = m.exchange(cid, q.get("code"), ver, ru=ru)
    return d


def part_redirects():
    st, c, _, _ = reg([RU, "http://127.0.0.1:9/cb", "http://[::1]:9/cb"])
    ok("Probe: Client mit https + Loopback registriert", st == 201, c)
    cid = c["client_id"]
    jar = jar_for(m.U)
    bad = ["https://CLIENT.example/cb", "https://client.example/CB", "https://client.example/cb/", "https://client.example:443/cb",
           "https://client.example/cb#x", "https://client.example/cb?x=1", "https://client.example/cb%2F", "http://client.example/cb",
           "https://client.example./cb", "https://cliеnt.example/cb", "https://client.example/cb/../cb", "http://localhost:9/cb",
           "http://127.0.0.1:10/cb", "http://127.0.0.1/cb", "http://[0:0:0:0:0:0:0:1]:9/cb", " https://client.example/cb",
           "https://client.example/cb\n", "//client.example/cb", "https://evil.example/cb"]
    leaks = {}
    for u in bad:
        _, _, st, _, hd = start(cid, u, jar)
        if st != 400 or "location" in hd:
            leaks[u] = (st, hd.get("location"))
    ok(f"Probe: {len(bad)} Abwandlungen der redirect_uri -> Fehlerseite ohne Umleitung", not leaks, leaks)
    # Fehler nach gueltiger redirect_uri: nur dorthin, mit state und iss
    _, _, st, _, hd = start(cid, RU, jar, method="plain")
    loc = hd.get("location") or ""
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(loc).query))
    ok("Probe: Fehler (PKCE plain) nur an die registrierte URI, mit state und iss", st == 303 and loc.startswith(RU + "?")
       and q.get("error") == "invalid_request" and q.get("state") == "st-1" and q.get("iss") == BASE, loc)
    # Registrierung: gefaehrliche Adressen
    evil = ["javascript:alert(1)", "data:text/html,x", "http://evil.example/cb", "http://localhost.evil.example/cb",
            "http://127.0.0.1.nip.io/cb", "http://0.0.0.0/cb", "https://u:p@client.example/cb", "https://client.example/cb#f",
            "https://client.example/cb#", "ftp://client.example/cb", "com.example.app:/cb", "https:///cb", "https://client.example:99999/cb",
            "https://a b.example/cb", "https://client.example/\u0000", "x" * 600]
    got = {u[:40]: reg([u])[0] for u in evil}
    ok(f"Probe: Registrierung lehnt {len(evil)} gefaehrliche redirect_uris ab", set(got.values()) == {400}, got)
    return cid


def part_xss():
    xn = '<img src=x onerror=alert(1)>"\'&'
    ru = 'https://client.example/cb?a="><svg/onload=alert(2)>'
    st, c, _, _ = reg([ru], name=xn)
    ok("Probe: Client mit HTML im Namen/URI registriert (Angreifer-Daten)", st == 201, c)
    cid = c.get("client_id")
    jar = jar_for(m.U)
    _, _, st, _, hd = start(cid, ru, jar)
    st, _, page, hd2 = req("GET", hd.get("location") or "/", jar=jar)
    ok("Probe: Zustimmungsseite escaped Client-Name und URI (kein <img src=x, kein <svg)", st == 200 and "<img src=x" not in page
       and "&lt;img src=x" in page and "<svg" not in page, re.findall(r".{0,80}img src=x.{0,80}", page))
    # Fehlerseite mit Ruecksprung-Link (Gruppe ohne API-Recht): href escaped, nur registrierte URI
    m.settings_api(group="oa-nur-diese-gruppe")
    try:
        _, _, st, _, hd = start(cid, ru, jar)
        st, _, page, _ = req("GET", hd.get("location") or "/", jar=jar)
        hrefs = re.findall(r'href="([^"]*)"', page)
        back = [h for h in hrefs if "client.example" in h]
        ok("Probe: Fehlerseite (kein API-Recht) - Link zurueck escaped, error=access_denied", st == 403 and "<svg" not in page
           and back and "error=access_denied" in back[0].replace("&amp;", "&"), (st, back))
    finally:
        m.settings_api(group="")
    # CSP-Kopf: Zeichen wie ; und * im Host duerfen keine Direktiven bzw. Platzhalter einschleusen
    res = {}
    for u in ("https://a.example;sandbox/cb", "https://*/cb", "https://a.example'/cb", "https://a.example,b/cb"):
        st, c2, _, _ = reg([u], name="Testclient csp")
        if st != 201:
            res[u] = ("abgelehnt", st)
            continue
        _, _, st, _, hd = start(c2["client_id"], u, jar)
        st, _, _, hd2 = req("GET", hd.get("location") or "/", jar=jar)
        res[u] = ("angenommen", (hd2.get("content-security-policy") or "").split("form-action", 1)[-1])
    # IDN in Unicode-Schreibweise: Kopf Content-Security-Policy muss latin-1 bleiben (sonst 500), Anzeige nicht verwechselbar
    for u in ("https://cliеnt.example/cb", "https://bücher.example/cb"):
        st, c2, _, _ = reg([u], name="Testclient idn")
        if st != 201:
            res[u] = ("abgelehnt", st)
            continue
        _, _, st, _, hd = start(c2["client_id"], u, jar)
        st, _, _, hd2 = req("GET", hd.get("location") or "/", jar=jar)
        res[u] = ("angenommen", st)
    inj = {u: v for u, v in res.items() if v[0] == "angenommen"}
    ok("Probe: Host mit ; * ' , bzw. Unicode wird nicht registriert (kein Einschleusen in CSP form-action, kein 500)", not inj, inj)


def part_scopes(cid):
    jar = jar_for(m.U)
    errs = {}
    for sc in ("mcp webhooks", "tel_out", "TEL", "tel:call", "admin", "read:pages write:everything", "mcp read:pages\x00tel"):
        _, _, st, _, hd = start(cid, RU, jar, scope=sc)
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(hd.get("location") or "").query))
        errs[sc] = q.get("error") or st
    ok("Probe: scope webhooks/tel*/unbekannt -> invalid_scope", set(errs.values()) == {"invalid_scope"}, errs)
    res = {}
    for r in ("https://evil.example/mcp", BASE + "/api/v1", BASE + "/mcpx", BASE, BASE + "/mcp/../api/v1"):
        _, _, st, _, hd = start(cid, RU, jar, resource=r)
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(hd.get("location") or "").query))
        res[r] = q.get("error") or st
    ok("Probe: resource ausser BASE/mcp -> invalid_target", set(res.values()) == {"invalid_target"}, res)
    # Formular mit Bereichen, die nicht angefragt waren (und webhooks/tel) -> nicht vergeben
    m.JAR = jar
    ver, ch = m.pkce()
    st, q, _ = m.consent(cid, ch, ru=RU, scope="read:pages", scopes=["read:pages", "write:pages", "webhooks", "tel_out", "write:db"])
    st, d, _, _ = m.exchange(cid, q.get("code"), ver, ru=RU)
    got = set((d.get("scope") or "").split())
    ok("Probe: Formular kann keine zusaetzlichen Bereiche erzwingen (nur mcp read:pages)", got == {"mcp", "read:pages"}, d)
    st, d2, _, _ = m.token({"grant_type": "refresh_token", "client_id": cid, "refresh_token": d.get("refresh_token", ""),
                            "scope": "mcp read:pages write:pages"})
    ok("Probe: Refresh mit mehr Bereichen -> invalid_scope", st == 400 and d2.get("error") == "invalid_scope", d2)
    st, d3, _, _ = m.token({"grant_type": "refresh_token", "client_id": cid, "refresh_token": d.get("refresh_token", ""),
                            "scope": "webhooks"})
    ok("Probe: Refresh mit webhooks -> invalid_scope", st == 400 and d3.get("error") == "invalid_scope", d3)
    acc = d.get("access_token", "")
    sts = {p: req("GET", p, headers={"X-API-Key": acc})[0] for p in ("/api/v1/me", "/api/v1/webhooks", "/api/v1/tasks")}
    ok("Probe: sgo_ im X-API-Key an /api/v1 -> 401", set(sts.values()) == {401}, sts)


def part_origin(cid):
    jar = jar_for(m.U)
    cases = {"Origin null + cross-site": ({"Origin": "null", "Sec-Fetch-Site": "cross-site"}, True, 403),
             "Origin null + same-site": ({"Origin": "null", "Sec-Fetch-Site": "same-site"}, True, 403),
             "fremder Origin ohne Sec-Fetch-Site": ({"Origin": "https://evil.example"}, True, 403),
             "Origin null ohne CSRF": ({"Origin": "null", "Sec-Fetch-Site": "same-origin"}, False, 403),
             "BASE-Origin mit Schraegstrich + gross": ({"Origin": BASE.upper() + "/", "Sec-Fetch-Site": "same-origin"}, True, 303)}
    res = {}
    for name, (h, with_csrf, want) in cases.items():
        _, _, st, _, hd = start(cid, RU, jar)
        rid = (hd.get("location") or "").split("r=", 1)[-1]
        st, _, _, hd2 = req("POST", "/oauth/authorize", form={"csrf": m.CSRF if with_csrf else "x", "r": rid, "action": "allow",
                                                              "scope": ["mcp", "read:pages"]}, jar=jar, headers=h)
        res[name] = (st, want, "code=" in (hd2.get("location") or ""))
    bad = {k: v for k, v in res.items() if v[0] != v[1] or (v[1] == 403 and v[2])}
    ok("Probe: Zustimmung - Origin/Sec-Fetch-Site/CSRF-Faelle", not bad, res)
    # Ablehnen ohne frische Bestaetigung: geht (nur access_denied, nie ein Code)
    old = Jar(session(m.U, m.CSRF, sec_at=time.time() - 3600))
    _, _, st, _, hd = start(cid, RU, old)
    rid = (hd.get("location") or "").split("r=", 1)[-1]
    st, _, _, hd2 = req("POST", "/oauth/authorize", form={"csrf": m.CSRF, "r": rid, "action": "deny"}, jar=old,
                        headers={"Origin": BASE, "Sec-Fetch-Site": "same-origin"})
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(hd2.get("location") or "").query))
    ok("Probe: Ablehnen ohne Bestaetigung -> access_denied, kein Code", st == 303 and q.get("error") == "access_denied" and "code" not in q, q)
    _, _, st, _, hd = start(cid, RU, old)
    rid = (hd.get("location") or "").split("r=", 1)[-1]
    st, _, page, hd2 = req("POST", "/oauth/authorize", form={"csrf": m.CSRF, "r": rid, "action": "allow", "scope": ["mcp", "read:pages"]},
                           jar=old, headers={"Origin": BASE, "Sec-Fetch-Site": "same-origin"})
    ok("Probe: Erlauben mit alter Bestaetigung -> kein Code", "location" not in hd2 and 'name="secret"' in page, st)


def _parallel(n, fn):
    out = [None] * n
    bar = threading.Barrier(n)

    def run(i):
        bar.wait()
        out[i] = fn()
    th = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for x in th:
        x.start()
    for x in th:
        x.join()
    return out


def part_race(cid):
    jar = jar_for(m.U)
    d = grant(cid, RU, jar)
    ref = d.get("refresh_token", "")
    out = _parallel(4, lambda: m.token({"grant_type": "refresh_token", "client_id": cid, "refresh_token": ref}))
    sts = [o[0] for o in out]
    wins = [o[1] for o in out if o[0] == 200]
    ok(f"Probe: 4 gleichzeitige Refresh mit demselben Token -> hoechstens einer 200 ({sts})", len(wins) <= 1, sts)
    alive = [m.mcp_ok(w["access_token"]) for w in wins]
    ok(f"Probe: danach Kette gesperrt (Wiederverwendung erkannt), auch das Token des Gewinners ({alive})", not any(alive), alive)
    # Code gleichzeitig zweimal einloesen
    m.JAR = jar
    ver, ch = m.pkce()
    st, q, _ = m.consent(cid, ch, ru=RU)
    out = _parallel(4, lambda: m.exchange(cid, q.get("code"), ver, ru=RU))
    sts = [o[0] for o in out]
    wins = [o[1] for o in out if o[0] == 200]
    ok(f"Probe: 4 gleichzeitige Code-Einloesungen -> hoechstens eine 200 ({sts})", len(wins) <= 1, sts)
    ok("Probe: Code doppelt (gleichzeitig) -> auch das ausgegebene Token gesperrt", not any(m.mcp_ok(w["access_token"]) for w in wins))


def part_lifecycle(cid):
    # Gruppe verliert API-Recht -> sofort ungueltig, Refresh verweigert
    d = grant(cid, RU, jar_for(m.U))
    acc, ref = d.get("access_token", ""), d.get("refresh_token", "")
    ok("Probe: Token gilt", m.mcp_ok(acc))
    m.settings_api(group="oa-nur-diese-gruppe")
    try:
        ok("Probe: Gruppe ohne API-Recht -> Zugriffstoken sofort ungueltig", not m.mcp_ok(acc))
        st, d2, _, _ = m.token({"grant_type": "refresh_token", "client_id": cid, "refresh_token": ref})
        ok("Probe: Gruppe ohne API-Recht -> Refresh verweigert", st == 400 and "access_token" not in d2, d2)
    finally:
        m.settings_api(group="")
    # Admin "API-Tokens widerrufen" (apitok.revoke(uid)) trennt auch verbundene Apps
    d = grant(cid, RU, jar_for(m.U))
    apitok.revoke(m.U)
    ok("Probe: Admin-Widerruf -> Zugriffstoken ungueltig", not m.mcp_ok(d.get("access_token", "")))
    st, d2, _, _ = m.token({"grant_type": "refresh_token", "client_id": cid, "refresh_token": d.get("refresh_token", "")})
    ok("Probe: Admin-Widerruf -> Refresh verweigert", st == 400 and "access_token" not in d2, d2)
    # Konto loeschen
    d = grant(cid, RU, jar_for(m.U2))
    ok("Probe: Token des zweiten Kontos gilt", m.mcp_ok(d.get("access_token", "")))
    delete_user_data(m.U2, drop_user=True)
    with db.connect() as c:
        left = c.execute("SELECT COUNT(*) FROM oauth_grant WHERE uid=?", (m.U2,)).fetchone()[0] + \
            c.execute("SELECT COUNT(*) FROM api_token WHERE uid=?", (m.U2,)).fetchone()[0]
    ok("Probe: Konto geloescht -> Token ungueltig, keine Verbindungen/Tokens uebrig", not m.mcp_ok(d.get("access_token", "")) and left == 0, left)
    st, d2, _, _ = m.token({"grant_type": "refresh_token", "client_id": cid, "refresh_token": d.get("refresh_token", "")})
    ok("Probe: Konto geloescht -> Refresh verweigert", st == 400 and "access_token" not in d2, d2)
    auth.create_user(m.U2, None, m.U2)
    # Verbindungen sind je Konto getrennt: Liste des zweiten Kontos zeigt nichts vom ersten
    grant(cid, RU, jar_for(m.U))
    st, a, _, _ = req("GET", "/api/account/oauth-apps", jar=jar_for(m.U2), headers={"X-Saga": "1"})
    ok("Probe: Verbundene Apps nur des eigenen Kontos", st == 200 and a.get("apps") == [], a)


def part_meta():
    hs = {"Host": "evil.example", "X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "http", "Forwarded": "host=evil.example"}
    st, d, _, _ = req("GET", "/.well-known/oauth-authorization-server", headers=hs)
    st2, d2, _, _ = req("GET", "/.well-known/oauth-protected-resource/mcp", headers=hs)
    blob = repr(d) + repr(d2)
    ok("Probe: Metadaten mit fremdem Host/X-Forwarded-Host -> nur BASE_URL", "evil" not in blob and d.get("issuer") == BASE
       and d2.get("resource") == BASE + "/mcp", blob[:300])
    st, _, _, hd = req("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers=hs)
    ok("Probe: WWW-Authenticate an /mcp mit fremdem Host -> resource_metadata aus BASE_URL",
       st == 401 and f'resource_metadata="{BASE}/' in (hd.get("www-authenticate") or "") and "evil" not in (hd.get("www-authenticate") or ""), hd)


def main():
    m.cleanup()
    prev = dict(auth.settings(fresh=True).get("api") or {})
    try:
        auth.create_user(m.U, m.PW, "Olga Auth")
        auth.create_user(m.U2, None, m.U2)
        m.settings_api(enabled=True, mcp=True, oauth=True, rate=3000, rate_write=1000, max_tokens=50, group="")
        part_meta()
        cid = part_redirects()
        part_xss()
        part_scopes(cid)
        part_origin(cid)
        part_race(cid)
        part_lifecycle(cid)
    finally:
        auth.save_setting("api", prev)
        m.cleanup()
        with db.connect() as c, c:
            c.execute("DELETE FROM oauth_client WHERE name LIKE 'Testclient%' OR name LIKE '<img%'")
    r = m.res
    print(f"\nMCP-OAuth-Proben {sum(r)}/{len(r)} ({time.time() - m.T0:.0f} s)", "- ALLES GRUEN" if r and all(r) else "- FEHLER", flush=True)
    return 0 if r and all(r) else 1


if __name__ == "__main__":
    sys.exit(main())
