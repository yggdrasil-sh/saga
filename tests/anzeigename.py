"""Bughunt D01: Personen ohne Namen nie als rohe Kennung ('~' + 16 Zeichen) zeigen, sondern lesbar - "Unbekannte Person
(spqz)" bzw. "Person auf b.example (spqz)" in der Sprache der Anfrage; Anzeigename im Konto (auch allein) speichern.
Wegwerf-Konten dn-*, raeumt auf. Umgebung: HK_TEST_BASE, BASE_URL, SESSION_SECRET, Postgres ueber db.py."""
import base64, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request
from itsdangerous import TimestampSigner
import auth
from db import connect

B = os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099")
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
res = []
RAW = re.compile(r"~[a-z2-7]{16}")


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, ("  -- " + str(info)[:500]) if (info and not c) else "", flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def call(u, m, p, b=None, lang="de"):
    h = {"Host": HOST, "Cookie": "saga=" + ck(u), "X-Saga": "1", "Content-Type": "application/json", "Accept-Language": lang}
    try:
        with urllib.request.urlopen(urllib.request.Request(B + p, data=json.dumps(b).encode() if b is not None else None,
                                                           method=m, headers=h), timeout=20) as r:
            raw, st = r.read(), r.status
    except urllib.error.HTTPError as e:
        raw, st = e.read(), e.code
    try:
        return st, json.loads(raw or b"null")
    except ValueError:
        return st, raw


def cleanup():
    with connect() as c, c:
        c.execute("DELETE FROM chat_member WHERE uid LIKE 'dn-%'")
        c.execute("DELETE FROM chat WHERE dm_key LIKE 'dn-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'dn-%'")


def main():
    cleanup()
    auth.save_setting("chat_enabled", True)
    auth.create_user("dn-anna", None, "Anna Probe", email="anna@example.org")
    auth.create_user("dn-bert", None, "")
    time.sleep(1.2)
    from saga.core import local_ref, unnamed
    from saga import i18n, sharing
    ref = local_ref("dn-bert")
    tag = ref[-4:]

    # --- Personensuche und Chat: lesbarer Ersatz in der Sprache der Anfrage -------------------------------------------
    s, d = call("dn-anna", "GET", "/api/chat/people?q=" + urllib.parse.quote(ref))
    hit = ((d or {}).get("users") or [{}])[0]
    ok("Chat-Personensuche: Name 'Unbekannte Person (…)' statt Kennung", s == 200 and hit.get("username") == ref
       and hit.get("name") == f"Unbekannte Person ({tag})" and not hit.get("sub"), hit)
    s, d = call("dn-anna", "GET", "/api/chat/people?q=" + urllib.parse.quote(ref), lang="en")
    hit = ((d or {}).get("users") or [{}])[0]
    ok("Englisch: 'Unknown person (…)'", hit.get("name") == f"Unknown person ({tag})", hit)
    s, ch = call("dn-anna", "POST", "/api/chats", {"kind": "dm", "user": ref})
    c = (ch or {}).get("chat") or {}
    other = [m for m in c.get("members", []) if not m.get("me")]
    ok("Direkt-Chat: Chat-Name und Mitglied lesbar, keine Kennung als Name", s == 200 and c.get("name") == f"Unbekannte Person ({tag})"
       and other and other[0].get("name") == f"Unbekannte Person ({tag})", c)
    s, lst = call("dn-bert", "GET", "/api/chats")
    mine = [x for x in (lst or {}).get("chats", lst if isinstance(lst, list) else []) if x.get("id") == c.get("id")]
    ok("Chat-Liste bei Bert: Anna mit Anzeigenamen", mine and mine[0].get("name") == "Anna Probe", mine)
    blob = json.dumps(call("dn-anna", "GET", "/api/chats")[1])
    ok("Chat-Liste bei Anna: keine rohe Kennung als Name", '"name": "' + ref not in blob and f"Unbekannte Person ({tag})" in blob, blob[:300])

    # --- Personen anderer Server ----------------------------------------------------------------------------------------
    i18n.set_lang("de")
    h = "~abcdefghijklmnop"
    ok("Fremd ohne Namen: 'Person auf b.example (mnop)'", sharing.remote_label("@" + h + "@b.example") == "Person auf b.example (mnop)")
    ok("Fremd mit Kennung als gespeichertem Namen: ebenso", sharing.remote_label("@" + h + "@b.example", h) == "Person auf b.example (mnop)")
    ok("Fremd mit Namen: 'Bert (b.example)'", sharing.remote_label("@" + h + "@b.example", "Bert") == "Bert (b.example)")
    ok("Oeffentlicher Name ohne Anzeigename: 'bert@b.example'", sharing.remote_label("@bert@b.example") == "bert@b.example")
    ok("unnamed lokal", unnamed(ref) == f"Unbekannte Person ({tag})")

    # --- Anzeigename im Konto: allein speichern, E-Mail bleibt; Kennung als Name abgelehnt ------------------------------
    s, d = call("dn-anna", "PATCH", "/api/account", {"name": "Anna Neu"})
    s2, a = call("dn-anna", "GET", "/api/account")
    ok("PATCH nur mit Namen: Name neu, E-Mail unveraendert", s == 200 and a.get("name") == "Anna Neu" and a.get("email") == "anna@example.org", (s, d, a))
    s, d = call("dn-anna", "PATCH", "/api/account", {"email": "anna2@example.org"})
    s2, a = call("dn-anna", "GET", "/api/account")
    ok("PATCH nur mit E-Mail: Name bleibt", s == 200 and a.get("name") == "Anna Neu" and a.get("email") == "anna2@example.org", a)
    s, d = call("dn-bert", "PATCH", "/api/account", {"name": h})
    ok("Kennung als Anzeigename wird abgelehnt", s == 400, (s, d))
    s, d = call("dn-bert", "PATCH", "/api/account", {"name": "Bert Berg"})
    s2, d = call("dn-anna", "GET", "/api/chat/people?q=" + urllib.parse.quote(ref))
    hit = ((d or {}).get("users") or [{}])[0]
    ok("Nach dem Speichern sieht Anna Berts Namen", s == 200 and hit.get("name") == "Bert Berg", hit)
    s, d = call("dn-bert", "PATCH", "/api/account", {"name": ""})
    s2, d = call("dn-anna", "GET", "/api/chat/people?q=" + urllib.parse.quote(ref))
    hit = ((d or {}).get("users") or [{}])[0]
    ok("Name wieder geloescht (optional): wieder der Ersatz", s == 200 and hit.get("name") == f"Unbekannte Person ({tag})", hit)


if __name__ == "__main__":
    try:
        main()
    finally:
        cleanup()
    n = sum(res)
    print(f"\nANZEIGENAME {n}/{len(res)} - " + ("ALLES GRUEN" if n == len(res) else "FEHLER"))
    sys.exit(0 if n == len(res) else 1)
