"""2.5 P6-5: @Erwaehnungen von Personen in Seiten und Kommentaren (saga/mentions.py, note_mention, page.mention,
comment.mention). Konten mit Markern als Anmeldenamen ('em-<16 hex>', Anzeigenamen ohne Marker). Geprueft:
1. Auswahl: GET /api/notes/{id}/people nur Personen mit Zugriff; Speichern: span.hk-mention data-u nur mit Handle, Person
   ohne Zugriff -> zu Text (kein Index, keine Mitteilung), Anmeldename/Unsinn in data-u -> Tag weg.
2. GET Seite: people-Woerterbuch {ref: {name, me}} fuer die Erwaehnungen, aktueller Name nach Umbenennen.
3. Mitteilung page.mention: entprellt (erst nach SAGA_MENTION_DELAY, Rueckgaengig vorher -> keine), genau einmal je Seite
   und Person (entfernen + wieder einfuegen -> keine neue), nicht an sich selbst, abschaltbar, Push-Kategorie comment, Titel
   nur mit Zugriff, Sprung zum Block (url mit Block-ID).
4. Still: Duplikat einer Seite, Versions-Wiederherstellung, Verschieben (blocks/move).
5. "Teilen und erwaehnen": erst teilen (PUT /shares), dann speichern -> Erwaehnung bleibt, Mitteilung.
6. Kommentare: Token "<@~handle>" -> comment.mention (statt comment.new), people in der Kommentarliste, Auszug mit '@Name'.
7. Lesepfade ohne Anmeldenamen (Marker-Scan): GET Seite, Kommentare, Mitteilungen, Markdown-Export, /api/v1 (Markdown und
   Bloecke), Webhook-Nutzlast nicht noetig (enthaelt keinen Inhalt); veroeffentlichte Seite: nur Text, kein data-u.
8. Kontoloeschung: Index weg, Anzeige "Geloeschte Person".
Wegwerf-Konten em-*, raeumt auf. Der Server muss mit SAGA_MENTION_DELAY=1 laufen (Standard 20 s; sonst wartet der Test
entsprechend laenger: MENTION_DELAY=<s>). Umgebung wie die anderen Tests (HK_TEST_BASE, BASE_URL, SESSION_SECRET)."""
import base64
import http.client
import json
import os
import re
import secrets
import sys
import time
import urllib.parse
from itsdangerous import TimestampSigner

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import auth  # noqa: E402
from db import connect  # noqa: E402

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
DELAY = float(os.environ.get("MENTION_DELAY", os.environ.get("SAGA_MENTION_DELAY", "1")))
res, SEEN = [], []
MK = lambda: "em-" + secrets.token_hex(8)        # noqa: E731
A, B, C, D = MK(), MK(), MK(), MK()               # Anna (Eigentuemerin), Bert (Bearbeiten), Cora (Lesen), Dora (ohne Zugriff)
NAMES = {A: "Anna Fuenf", B: "Bert Fuenf", C: "Cora Fuenf", D: "Dora Fuenf"}


def ok(n, c, detail=None):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, flush=True)
    if not c and detail is not None:
        print("     ", str(detail)[:1500], flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5, "sec_at": int(time.time())}).encode())).decode()


def req(m, p, u=A, body=None, hdr=None, scan=True, raw=False):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=60)
    h = {"Host": HOST, "Accept-Language": "de"}
    if u:
        h.update({"X-Saga": "1", "Cookie": "saga=" + ck(u)})
    h.update(hdr or {})
    data = None
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    c.request(m, p, body=data, headers=h)
    r = c.getresponse()
    out = r.read()
    if scan:
        SEEN.append((u, f"{m} {p}", out.decode("utf-8", "replace")))
    if raw:
        return r.status, out.decode("utf-8", "replace")
    try:
        return r.status, json.loads(out) if out[:1] in (b"[", b"{") else out
    except ValueError:
        return r.status, out


def handle(u):
    from saga.core import local_ref
    return local_ref(u)


def M(u, txt=None):
    return f'<span class="hk-mention" data-u="{handle(u)}">{txt or NAMES[u]}</span>'


def P(i, text):
    return {"id": i, "type": "paragraph", "data": {"text": text}}


def save(nid, blocks, u=A, rev=None):
    b = {"body": json.dumps({"blocks": blocks}), "format": "blocks"}
    if rev is not None:
        b["rev"] = rev
    return req("PATCH", f"/api/notes/{nid}", u, b)


def get(nid, u=A):
    s, d = req("GET", f"/api/notes/{nid}", u)
    return d if s == 200 else {}


def body_text(d):
    return json.dumps(json.loads(d.get("body") or '{"blocks": []}')["blocks"], ensure_ascii=False)


def notes(u, kind=None):
    s, d = req("GET", "/api/notifications?limit=50", u)
    items = d.get("items", []) if isinstance(d, dict) else []
    return [x for x in items if kind is None or x["kind"] == kind]


def wait_dispatch():
    time.sleep(DELAY + 2.5)


def rows(nid):
    with connect() as c:
        return {r[0]: r[1:] for r in c.execute("SELECT person, block_id, first_by, gone_at, notified FROM note_mention WHERE note_id=?", (nid,)).fetchall()}


def run():
    s, d = req("POST", "/api/notes", A, {"title": "Planung"})
    nid = d["id"]
    for u, role in ((B, "edit"), (C, "view")):
        s, _ = req("PUT", f"/api/notes/{nid}/shares", A, {"type": "user", "target": handle(u), "role": role})
        ok(f"geteilt mit {NAMES[u]} ({role}) ({s})", s == 200)

    # --- 1. Auswahl und Speicherform --------------------------------------------------------------------------------
    s, d = req("GET", f"/api/notes/{nid}/people?q=fuenf", A)
    refs = {x["ref"] for x in d.get("people", [])}
    ok("Auswahl: Anna, Bert, Cora - nicht Dora (ohne Zugriff)", s == 200 and refs == {handle(A), handle(B), handle(C)}, d)
    s, d = req("GET", f"/api/notes/{nid}/people", D)
    ok(f"Auswahl ohne Zugriff -> 404 ({s})", s == 404)
    s, d = save(nid, [P("b1", "Hallo " + M(B) + " und " + M(D) + " und " + f'<span class="hk-mention" data-u="{B}">roh</span>'
                         + ' <span class="hk-mention" data-u="~zzzz">x</span>')])
    ok(f"Speichern mit Erwaehnungen ({s})", s == 200, d)
    st = get(nid)
    txt = body_text(st)
    ok("Bert als Handle gespeichert", f'data-u=\\"{handle(B)}\\"' in txt, txt)
    ok("Dora (ohne Zugriff) nur Text, keine Erwaehnung", handle(D) not in txt and "Dora Fuenf" in txt, txt)
    ok("Anmeldename/Unsinn in data-u -> Tag weg, Text bleibt", "roh" in txt and B not in txt and "~zzzz" not in txt, txt)
    pm = st.get("people") or {}
    ok("people-Woerterbuch: Bert mit Anzeigename, nicht me", pm.get(handle(B), {}).get("name") == "Bert Fuenf" and pm[handle(B)]["me"] is False, pm)
    r = rows(nid)
    ok("note_mention: nur Bert, block b1, von Anna, noch nicht mitgeteilt", set(r) == {B} and r[B][0] == "b1" and r[B][1] == A and r[B][3] is None, r)
    ok("noch keine Mitteilung (entprellt)", not notes(B, "page.mention"))
    wait_dispatch()
    nb = notes(B, "page.mention")
    ok("Bert: genau eine page.mention", len(nb) == 1, nb)
    it = nb[0] if nb else {}
    ok("Mitteilung: Text mit Anzeigename, Titel, Sprung zum Block", "Anna Fuenf" in it.get("text", "") and it.get("title") == "Planung"
       and it.get("url") == f"/notes#{nid}:b1" and it.get("block_id") == "b1", it)
    ok("Dora: keine Mitteilung", not notes(D, "page.mention"))

    # --- 3. einmal je Seite und Person, Rueckgaengig, sich selbst ------------------------------------------------------
    save(nid, [P("b1", "Hallo")])
    ok("Entfernt -> gone_at gesetzt", rows(nid)[B][2] is not None)
    save(nid, [P("b1", "Hallo"), P("b2", "Nochmal " + M(B))])
    ok("Wieder eingefuegt -> gone_at leer, Block b2", rows(nid)[B][2] is None and rows(nid)[B][0] == "b2", rows(nid))
    wait_dispatch()
    ok("erneutes Einfuegen: keine zweite Mitteilung", len(notes(B, "page.mention")) == 1)
    save(nid, [P("b1", "Hallo " + M(C))])
    save(nid, [P("b1", "Hallo")])                       # Tippfehler: sofort wieder weg
    wait_dispatch()
    ok("Rueckgaengig vor Ablauf: Cora bekommt nichts", not notes(C, "page.mention"))
    save(nid, [P("b1", "Ich " + M(A))])
    wait_dispatch()
    ok("sich selbst erwaehnen: keine Mitteilung", not notes(A, "page.mention"))
    # Bert erwaehnt Anna (Eigentuemerin) auf einer Unterseite -> Anna bekommt eine
    s, d = req("POST", "/api/notes", B, {"title": "Unterseite", "parent_id": nid})
    sub = d.get("id")
    save(sub, [P("b3", "Frage an " + M(A))], u=B)
    wait_dispatch()
    na = notes(A, "page.mention")
    ok("Eigentuemerin von Bert erwaehnt: Mitteilung mit Berts Namen", len(na) == 1 and "Bert Fuenf" in na[0].get("text", ""), na)

    # --- abschaltbar ---------------------------------------------------------------------------------------------------
    s, d = req("GET", "/api/notifications/settings", C)
    kinds = [x["kind"] for x in d.get("kinds", [])]
    ok("Einstellungen: page.mention und comment.mention abschaltbar", "page.mention" in kinds and "comment.mention" in kinds, kinds)
    req("PUT", "/api/notifications/settings", C, {"page.mention": False})
    save(nid, [P("b1", "Lesen: " + M(C))])
    wait_dispatch()
    ok("abgeschaltet: Cora bekommt nichts", not notes(C, "page.mention"))
    req("PUT", "/api/notifications/settings", C, {"page.mention": True})
    save(nid, [P("b1", "Lesen")])
    save(nid, [P("b1", "Lesen: " + M(C))])
    wait_dispatch()
    ok("schon gemeldet (Zeile da): nach dem Einschalten keine zweite", not notes(C, "page.mention"))
    from saga import push
    ok("Push-Kategorie comment umfasst page.mention", "page.mention" in push.CATS["comment"])

    # --- Name aendern -> aktueller Name -------------------------------------------------------------------------------
    with connect() as c, c:
        c.execute("UPDATE users SET display_name=? WHERE username=?", ("Bert Neu", B))
    save(nid, [P("b1", "Name: " + M(B, "Bert Fuenf"))])
    st = get(nid)
    ok("Umbenannt: people zeigt den aktuellen Namen", (st.get("people") or {}).get(handle(B), {}).get("name") == "Bert Neu", st.get("people"))

    # --- 4. still: Duplikat, Version, Verschieben ---------------------------------------------------------------------
    s, d = req("POST", f"/api/notes/{nid}/duplicate", A, {})
    dup = d.get("id")
    ok(f"Duplikat angelegt ({s})", s == 200 and dup, d)
    save(dup, json.loads(get(dup)["body"])["blocks"] + [P("x9", "mehr")])
    wait_dispatch()
    ok("Duplikat: keine Mitteilung an Bert (stand schon im Inhalt)", not [x for x in notes(B, "page.mention") if (x.get("note") or {}).get("id") == dup])
    s, d = req("GET", f"/api/notes/{nid}/versions", A)
    vs = d if isinstance(d, list) else None
    s2, d2 = req("POST", f"/api/notes/{nid}/versions/{vs[-1]['id']}/restore", A, {}) if vs else (0, None)
    ok(f"Version zurueck ({s2})", s2 == 200, d2)
    s, d = req("POST", "/api/notes", A, {"title": "Ziel"})
    tgt = d["id"]
    req("PUT", f"/api/notes/{tgt}/shares", A, {"type": "user", "target": handle(C), "role": "view"})
    cur = get(nid)
    save(nid, json.loads(cur["body"])["blocks"] + [P("mv1", "Zum Verschieben " + M(C))], rev=cur["rev"])
    cur = get(nid)
    s, d = req("POST", f"/api/notes/{nid}/blocks/move", A, {"ids": ["mv1"], "to": tgt, "rev_a": cur["rev"]})
    ok(f"Block mit Erwaehnung verschoben ({s})", s == 200, d)
    wait_dispatch()
    ok("Verschieben: keine Mitteilung an Cora fuer das Ziel", not [x for x in notes(C, "page.mention") if (x.get("note") or {}).get("id") == tgt])
    ok("Verschieben: Index im Ziel still nachgefuehrt", C in rows(tgt) and rows(tgt)[C][3] is not None, rows(tgt))

    # --- 5. Teilen und erwaehnen ---------------------------------------------------------------------------------------
    s, _ = req("PUT", f"/api/notes/{nid}/shares", A, {"type": "user", "target": handle(D), "role": "view"})
    cur = get(nid)
    save(nid, json.loads(cur["body"])["blocks"] + [P("b5", "Jetzt " + M(D))])
    wait_dispatch()
    nd = notes(D, "page.mention")
    ok("Teilen und erwaehnen: Dora bekommt die Mitteilung", len(nd) == 1 and nd[0].get("access") is True, nd)
    ok("Teilen und erwaehnen: Dora bekommt auch share.added", bool(notes(D, "share.added")))
    req("DELETE", f"/api/notes/{nid}/shares", A, {"type": "user", "target": handle(D)})
    nd = notes(D, "page.mention")
    ok("Zugriff entzogen: Mitteilung ohne Titel", nd and nd[0].get("access") is False and nd[0].get("title") != "Planung", nd)

    # --- 6. Kommentare --------------------------------------------------------------------------------------------------
    s, d = req("POST", f"/api/notes/{nid}/comments", B, {"block_id": "b1", "body": f"Schau mal <@{handle(A)}> und <@{handle(C)}> und <@{handle(D)}>"})
    ok(f"Kommentar mit Erwaehnungen ({s})", s == 200, d)
    time.sleep(0.5)
    ca = notes(A, "comment.mention")
    ok("Anna: comment.mention statt comment.new", len(ca) == 1 and not [x for x in notes(A, "comment.new") if x.get("comment_id") == d["comment"]["id"]], notes(A))
    ok("Cora: comment.mention", len(notes(C, "comment.mention")) == 1)
    ok("Dora (kein Zugriff mehr): nichts", not notes(D, "comment.mention"))
    ok("Auszug mit '@Name' statt Token", "@Anna Fuenf" in ca[0].get("excerpt", "") and "<@" not in ca[0].get("excerpt", ""), ca)
    s, d = req("GET", f"/api/notes/{nid}/comments", C)
    pm = d.get("people") or {}
    ok("Kommentarliste: people fuer die Token", pm.get(handle(A), {}).get("name") == "Anna Fuenf" and pm.get(handle(C), {}).get("me") is True, pm)

    # --- 7. Lesepfade ---------------------------------------------------------------------------------------------------
    s, md = req("GET", f"/api/notes/{nid}/export", A, raw=True)
    ok("Markdown-Export: '@Name' (Text), kein Handle", s == 200 and "@Dora Fuenf" in md and "hk-mention" not in md and "~" not in md, md[:300])
    with connect() as c, c:
        c.execute("INSERT INTO setting (k, v) VALUES ('api', ?) ON CONFLICT (k) DO UPDATE SET v=EXCLUDED.v", (json.dumps({"enabled": True}),))
    s, d = req("POST", "/api/account/api-tokens", A, {"name": "em", "scopes": ["read:pages"]})
    tok = d.get("token") if isinstance(d, dict) else None
    ok(f"API-Token ({s})", bool(tok), d)
    if tok:
        s, d = req("GET", f"/api/v1/pages/{nid}?format=markdown", None, hdr={"Authorization": "Bearer " + tok})
        ok("API v1 Markdown: Name statt Handle", s == 200 and "@Dora Fuenf" in d.get("markdown", "") and "~" not in d.get("markdown", ""), d)
        s, d = req("GET", f"/api/v1/pages/{nid}", None, hdr={"Authorization": "Bearer " + tok})
        raw = json.dumps(d)
        ok("API v1 Bloecke: Erwaehnung nur als Handle + people", s == 200 and handle(D) in raw and (d.get("people") or {}).get(handle(D)), raw[:400])
    s, d = req("PUT", "/api/account/public-name", A, {"name": "em" + secrets.token_hex(3)})
    s, d = req("PUT", f"/api/notes/{nid}/publish", A, {})
    url = d.get("url") or ""
    path = urllib.parse.urlparse(url).path
    s, html = req("GET", path, None, raw=True) if path else (0, "")
    ok(f"Veroeffentlicht: Name als Text, ohne data-u/Handle ({s})", s == 200 and "Dora Fuenf" in html and "data-u" not in html and handle(D) not in html, html[-600:])
    req("DELETE", f"/api/notes/{nid}/publish", A)

    # --- 8. Kontoloeschung ----------------------------------------------------------------------------------------------
    from saga.userdata import delete_user_data
    delete_user_data(C, drop_user=True)
    ok("Kontoloeschung: Index-Zeilen der Person weg", C not in rows(nid) and C not in rows(tgt))
    st = get(nid)
    ok("Erwaehnung eines geloeschten Kontos: 'Geloeschte Person'", any(v.get("gone") for v in (st.get("people") or {}).values())
       or handle(C) not in body_text(st), st.get("people"))


def scan():
    hits = []
    for who, what, txt in SEEN:
        for u in (A, B, C, D):
            if u != who and u in txt:
                hits.append((who, what, u, txt[max(0, txt.find(u) - 80):txt.find(u) + 40]))
    ok(f"Marker-Scan: {len(SEEN)} Antworten ohne Anmeldenamen anderer Konten", not hits, hits[:3])


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        us = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'em-%'").fetchall()]
    for u in us:
        delete_user_data(u, drop_user=True)
    with connect() as c, c:
        c.execute("DELETE FROM note WHERE uid LIKE 'em-%'")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'em-%' OR target LIKE 'em-%'")


def main():
    from saga.i18n import set_lang
    set_lang("de")
    cleanup()
    keep = auth.settings(fresh=True).get("share_enabled")
    auth.save_setting("share_enabled", True)
    for u in (A, B, C, D):
        auth.create_user(u, "Erwaehnen-Pass-1234", NAMES[u])
    time.sleep(5.6)                                   # Einstellungs-Zwischenspeicher des Servers
    try:
        run()
    except Exception:
        import traceback
        traceback.print_exc()
        res.append(False)
    finally:
        scan()
        cleanup()
        auth.save_setting("share_enabled", keep)


if __name__ == "__main__":
    main()
    print(f"\nERWAEHNUNGEN {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
    sys.exit(0 if res and all(res) else 1)
