"""Anmeldenamen anderer Konten nie zeigen (2.4, Testrunde W4): Personensuche (Teilen, Chats), Freigaben, "Mit mir geteilt",
Kommentare, Benachrichtigungen, Anwesenheit, Versionen, Seitensuche - in allen Antworten fuer andere Konten steht weder
der Anmeldename noch (ohne Anzeigenamen) ein Ersatz daraus, sondern Anzeigename, oeffentlicher Name bzw. ein pseudonymer
Handle ('~' + 16 Zeichen). Anfragen nehmen den Handle (und wie bisher den Benutzernamen) an.
Ausnahme (dokumentiert): Mitglieder eines GEMEINSAMEN Chats bzw. Anrufs - Chats v1/saga-call v1 binden Schluessel und
Signaturen an den Anmeldenamen; die Oberflaeche zeigt ihn dort trotzdem nicht.
Wegwerf-Konten an-*, raeumt auf. Umgebung: HK_TEST_BASE, BASE_URL, SESSION_SECRET, Postgres ueber db.py."""
import base64, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request
from itsdangerous import TimestampSigner
import auth
from db import connect

B = os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099")
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
res = []
SEEN = []          # (Pfad, Antwort) aller Antworten fuer Anna


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, ("  -- " + str(info)[:500]) if (info and not c) else "", flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def call(u, m, p, b=None, keep=True):
    h = {"Host": HOST, "Cookie": "saga=" + ck(u), "X-Saga": "1", "Content-Type": "application/json", "Accept-Language": "de"}
    try:
        with urllib.request.urlopen(urllib.request.Request(B + p, data=json.dumps(b).encode() if b is not None else None,
                                                           method=m, headers=h), timeout=20) as r:
            raw = r.read()
            st = r.status
    except urllib.error.HTTPError as e:
        raw, st = e.read(), e.code
    if keep:
        SEEN.append((u, m + " " + p, raw.decode("utf-8", "replace")))
    try:
        return st, json.loads(raw or b"null")
    except ValueError:
        return st, raw


def cleanup():
    auth.save_setting("search_username", True)
    with connect() as c, c:
        for t in ("note_comment", "notification", "note_share", "note_version", "user_kv"):
            col = "owner_uid" if t == "note_share" else "uid"
            c.execute(f"DELETE FROM {t} WHERE {col} LIKE 'an-%'")
        c.execute("DELETE FROM note WHERE uid LIKE 'an-%'")
        for t in ("chat_member",):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE 'an-%'")
        c.execute("DELETE FROM chat WHERE dm_key LIKE 'an-%'")
        c.execute("DELETE FROM public_name WHERE uid LIKE 'an-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'an-%'")


def leaks(viewer, other):
    """Antworten fuer viewer, in denen der Anmeldename von other steht (als ganzes Wort)."""
    rx = re.compile(r'(?<![\w.@-])' + re.escape(other) + r'(?![\w.-])')
    return [(p, raw[:300]) for u, p, raw in SEEN if u == viewer and rx.search(raw)]


def main():
    cleanup()
    auth.save_setting("share_enabled", True)
    auth.save_setting("chat_enabled", True)
    auth.create_user("an-anna", None, "Anna Nameprobe")
    auth.create_user("an-bert", None, "")                 # ohne Anzeigenamen: Ersatz darf nicht der Anmeldename sein
    auth.create_user("an-cora", None, "an-cora")          # Anzeigename = Anmeldename: ebenso
    time.sleep(1.2)

    # --- Personensuche ---------------------------------------------------------------------------------------------------
    s, d = call("an-anna", "GET", "/api/share/targets?q=an-bert")
    hit = (d.get("users") or [{}])[0] if isinstance(d, dict) else {}
    ref = hit.get("username", "")
    ok("Teilen-Suche: Treffer mit Handle statt Anmeldename", s == 200 and re.fullmatch(r"~[a-z2-7]{16}", ref or "") and hit.get("name") == ref, hit)
    s, d = call("an-anna", "GET", "/api/share/targets?q=an-cora")
    cora = (d.get("users") or [{}])[0]
    ok("Anzeigename gleich Anmeldename: Name ist ebenfalls der Handle", re.fullmatch(r"~[a-z2-7]{16}", cora.get("name", "")), cora)
    s, d = call("an-anna", "GET", "/api/chat/people?q=an-bert")
    ph = (d.get("users") or [{}])[0] if isinstance(d, dict) else {}
    ok("Chat-Personensuche: derselbe Handle", ph.get("username") == ref, ph)

    # --- Freigabe per Handle, Liste, Entfernen ----------------------------------------------------------------------------
    nid = call("an-anna", "POST", "/api/notes", {"title": "Namensprobe"})[1]["id"]
    s, st = call("an-anna", "PUT", f"/api/notes/{nid}/shares", {"type": "user", "target": ref, "role": "edit"})
    ok("Teilen mit dem Handle aus der Suche", s == 200 and any(x["target"] == ref for x in st.get("shares", [])), st)
    s, st = call("an-anna", "GET", f"/api/notes/{nid}/shares")
    ok("Freigabe-Liste zeigt Handle und Ersatznamen", s == 200 and [x["target"] for x in st["shares"]] == [ref], st)
    s, _ = call("an-anna", "PUT", f"/api/notes/{nid}/shares", {"type": "user", "target": "an-cora", "role": "view"})
    ok("Teilen mit Benutzername geht weiterhin (aeltere Apps)", s == 200)
    s, st = call("an-anna", "DELETE", f"/api/notes/{nid}/shares?type=user&target=" + urllib.parse.quote(cora["username"]))
    ok("Freigabe per Handle entfernen", s == 200 and st.get("removed") is True, st)

    # --- Bert arbeitet auf Annas Seite: Kommentar, Bearbeiten, Anwesenheit ----------------------------------------------
    rev = call("an-bert", "GET", f"/api/notes/{nid}", keep=False)[1].get("rev")
    call("an-bert", "PATCH", f"/api/notes/{nid}", {"body": json.dumps({"blocks": [{"type": "paragraph", "data": {"text": "von Bert"}}]}),
                                                   "format": "blocks", "rev": rev}, keep=False)
    call("an-bert", "POST", f"/api/notes/{nid}/comments", {"body": "Kommentar von Bert", "block_id": ""}, keep=False)
    call("an-bert", "POST", "/api/presence", {"note_id": nid, "editing": True}, keep=False)
    time.sleep(0.5)
    s, n = call("an-anna", "GET", f"/api/notes/{nid}")
    ok("Seite: zuletzt geaendert von - ohne Anmeldename", s == 200 and (n.get("updated_by") or {}).get("username") == ref, n.get("updated_by"))
    call("an-anna", "GET", f"/api/notes/{nid}/comments")
    s, nt = call("an-anna", "GET", "/api/notifications")
    ok("Benachrichtigung: Ausloeser als Handle", s == 200 and any((x.get("actor") or {}).get("username") == ref for x in nt.get("items", nt if isinstance(nt, list) else [])), nt)
    s, pr = call("an-anna", "POST", "/api/presence", {"note_id": nid, "editing": False})
    ok("Anwesenheit: Bert als Handle, Anna selbst mit eigenem Namen", s == 200 and any(u.get("username") == ref for u in pr.get("users", []))
       and any(u.get("me") and u.get("username") == "an-anna" for u in pr.get("users", [])), pr)
    call("an-anna", "GET", f"/api/notes/{nid}/versions")
    call("an-anna", "POST", "/api/notes/search", {"q": "namensprobe"})

    # --- Bert sieht Annas Seite: Eigentuemer ohne Anmeldename -----------------------------------------------------------
    s, sh = call("an-bert", "GET", "/api/shared")
    own = next((x.get("owner") for x in sh if x.get("id") == nid), None) if isinstance(sh, list) else None
    ok("Mit mir geteilt: Eigentuemerin mit Anzeigename, Kennung als Handle", own and own["name"] == "Anna Nameprobe"
       and re.fullmatch(r"~[a-z2-7]{16}", own["username"]), own)
    call("an-bert", "GET", f"/api/notes/{nid}")
    call("an-bert", "GET", f"/api/notes/{nid}/comments")
    call("an-bert", "POST", "/api/notes/search", {"q": "namensprobe"})
    call("an-bert", "GET", "/api/share/targets?q=an-anna")

    # --- Pruefung: in keiner dieser Antworten der Anmeldename des anderen Kontos ------------------------------------------
    la, lb = leaks("an-anna", "an-bert") + leaks("an-anna", "an-cora"), leaks("an-bert", "an-anna")
    ok(f"Keine Antwort an Anna enthaelt 'an-bert'/'an-cora' ({len([1 for u, _, _ in SEEN if u == 'an-anna'])} Antworten)", not la, la)
    ok(f"Keine Antwort an Bert enthaelt 'an-anna' ({len([1 for u, _, _ in SEEN if u == 'an-bert'])} Antworten)", not lb, lb)

    # --- Chat per Handle (Ausnahme: Mitglieder kennen sich fuer die Schluessel) -------------------------------------------
    s, ch = call("an-anna", "POST", "/api/chats", {"kind": "dm", "user": ref}, keep=False)
    c = (ch or {}).get("chat") or {}
    other = [m for m in c.get("members", []) if not m.get("me")]
    ok("Direkt-Chat mit dem Handle angelegt; Mitglied mit ref = Handle und Namen ohne Anmeldename", s == 200 and other
       and other[0].get("ref") == ref and other[0].get("name") == ref, c)
    ok("Fremder Handle wird nicht als Konto aufgeloest", call("an-anna", "POST", "/api/chats", {"kind": "dm", "user": "~" + "a" * 16}, keep=False)[0] in (400, 404))

    # --- Linux-Runde 2, N2: gleichnamige Personen unterscheidbar (Unterzeile: oeffentlicher Name bzw. Handle) ------------
    auth.create_user("an-z1", None, "Zora Gleich"); auth.create_user("an-z2", None, "Zora Gleich")
    from saga import publish
    publish.set_name("an-z2", "zora-zwei")
    for path in ("/api/share/targets?q=zora", "/api/chat/people?q=zora"):
        s, d = call("an-anna", "GET", path, keep=False)
        us = (d or {}).get("users") or []
        subs = sorted(u.get("sub", "") for u in us)
        ok(f"N2: {path.split('?')[0]}: zwei gleichnamige Treffer mit unterscheidbarer Unterzeile (Handle bzw. oeffentlicher Name)",
           len(us) == 2 and all(u["name"] == "Zora Gleich" for u in us) and len(set(subs)) == 2 and "zora-zwei" in subs
           and any(x.startswith("~") for x in subs) and not any("an-z" in x for x in subs), us)

    # --- Sicherheitsrunde 2, H2: Personensuche nicht mehr ueber den Anmeldenamen (Praefix) --------------------------------
    from saga.core import local_ref
    auth.create_user("an-xberg", None, "Xaver Berg")
    xr = local_ref("an-xberg")
    hits = lambda u, q: [x.get("username") for x in (call(u, "GET", "/api/share/targets?q=" + urllib.parse.quote(q), keep=False)[1] or {}).get("users", [])] \
        + [x.get("username") for x in (call(u, "GET", "/api/chat/people?q=" + urllib.parse.quote(q), keep=False)[1] or {}).get("users", [])]
    ok("H2: Praefix des Anmeldenamens trifft nicht (Zeichen fuer Zeichen nicht erratbar)",
       all(xr not in hits("an-anna", q) for q in ("an-x", "an-xb", "an-xber")), [hits("an-anna", q) for q in ("an-x", "an-xber")])
    ok("H2: Anzeigename (Teilwort) und genauer Handle treffen", hits("an-anna", "xaver") == [xr, xr] and hits("an-anna", "berg").count(xr) == 2
       and hits("an-anna", xr) == [xr, xr], (hits("an-anna", "xaver"), hits("an-anna", xr)))
    ok("H2: genauer Anmeldename trifft mit Admin-Schalter 'Suche nach Anmeldename' (Standard an)",
       auth.settings(fresh=True).get("search_username") is True and hits("an-anna", "an-xberg") == [xr, xr])
    auth.save_setting("search_username", False)
    time.sleep(5.6)                                                       # Einstellungen 5 s zwischengespeichert
    ok("H2: Schalter aus -> auch der genaue Anmeldename trifft nicht, Handle weiter",
       hits("an-anna", "an-xberg") == [] and hits("an-anna", xr) == [xr, xr])

    # --- Sicherheitsrunde 2, H1: kein Orakel fuer Anmeldenamen (Schluessel, Chat beginnen, Teilen) ------------------------
    nid2 = call("an-anna", "POST", "/api/notes", {"title": "Orakel"}, keep=False)[1]["id"]
    s1, b1 = call("an-anna", "POST", "/api/chats", {"kind": "dm", "user": "an-xberg"}, keep=False)
    s2, b2 = call("an-anna", "POST", "/api/chats", {"kind": "dm", "user": "an-niemand"}, keep=False)
    ok("H1: Chat per Anmeldename (Schalter aus): bestehendes Konto antwortet wie unbekanntes", s1 == s2 == 404 and b1 == b2, (s1, b1, s2, b2))
    s1, b1 = call("an-anna", "PUT", f"/api/notes/{nid2}/shares", {"type": "user", "target": "an-xberg", "role": "view"}, keep=False)
    s2, b2 = call("an-anna", "PUT", f"/api/notes/{nid2}/shares", {"type": "user", "target": "an-niemand", "role": "view"}, keep=False)
    ok("H1: Teilen per Anmeldename (Schalter aus): bestehendes Konto antwortet wie unbekanntes", s1 == s2 == 400 and b1 == b2, (s1, b1, s2, b2))
    s, _ = call("an-anna", "PUT", f"/api/notes/{nid2}/shares", {"type": "user", "target": "an-bert", "role": "view"}, keep=False)
    ok("H1: bekannte Person (gemeinsamer Chat) per Anmeldename weiter moeglich (aeltere Apps)", s == 200)
    s, kk = call("an-anna", "GET", "/api/chat/keys?users=" + ",".join(["an-xberg", "an-niemand", xr, "~" + "b" * 16]), keep=False)
    ok("H1: /api/chat/keys - fremdes, unbekanntes Konto und fremder Handle gleichermassen ohne Eintrag", s == 200 and kk == {"users": {}}, kk)
    s, kk = call("an-anna", "GET", "/api/chat/keys?users=" + ref + ",an-bert", keep=False)
    ok("H1: /api/chat/keys per Handle fuer Chat-Partner (Antwort unter dem Handle; null = noch keine Chats eingerichtet)",
       s == 200 and set(kk.get("users", {})) == {ref, "an-bert"} and kk["users"][ref] is None, kk)
    s, d = call("an-anna", "POST", "/api/chats", {"kind": "dm", "user": xr}, keep=False)
    ok("H1: Chat per Handle geht auch mit Schalter aus", s == 200, d)
    auth.save_setting("search_username", True)
    time.sleep(5.6)
    s, _ = call("an-anna", "PUT", f"/api/notes/{nid2}/shares", {"type": "user", "target": "an-cora", "role": "view"}, keep=False)
    ok("H1: Schalter an -> Teilen per genauem Anmeldenamen wie bisher", s == 200)

    cleanup()
    n = sum(res)
    print(f"\nANMELDENAMEN {n}/{len(res)} - {'ALLES GRUEN' if n == len(res) else 'FEHLER'}")
    sys.exit(0 if n == len(res) else 1)


if __name__ == "__main__":
    main()
