"""Glocke buendelt je Gespraechseinheit (Design-Runde Web, UX-29/30).

GET /api/notifications?group=1: Nachrichten derselben Person/desselben Chats -> EINE Zeile mit Zaehler (count, ids,
counts, group_key, first, actors); zwei Personen -> zwei Zeilen; Gruppen-Chat mit "(von A, B)"; verpasste Anrufe eines
Chats gebuendelt ("5 verpasste Videoanrufe"); Kommentare mehrerer Unterhaltungen auf derselben Seite -> "3 neue Kommentare";
andere Arten einzeln; unread_groups (Zeilen) neben unread (Mitteilungen); Gelesen per {groups: [...]} markiert die ganze
Einheit; Blaettern (limit zaehlt Zeilen, keine Mitteilung doppelt oder verloren); ohne ?group=1 unveraendert (Apps).
Wegwerf-Konten gb-*; Chats/Anrufe direkt in der Datenbank (ohne Schluessel - Inhalte kennt die Glocke ohnehin nicht).
Umgebung wie die anderen Tests (HK_TEST_BASE, BASE_URL, SESSION_SECRET)."""
import base64, http.client, json, os, sys, time, urllib.parse
from itsdangerous import TimestampSigner
import auth
from db import connect
from saga import notify

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
PW = "Glocke-Pass-1234"
res = []


def ok(n, c):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, cookie=None, body=None):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=30)
    h = {"Host": HOST, "Accept-Language": "de"}
    raw = None
    if cookie:
        h["Cookie"] = "saga=" + cookie; h["X-Saga"] = "1"
    if body is not None:
        h["Content-Type"] = "application/json"; raw = json.dumps(body)
    c.request(m, p, body=raw, headers=h)
    r = c.getresponse(); data = r.read()
    try:
        data = json.loads(data)
    except ValueError:
        data = data.decode("utf-8", "replace")
    c.close()
    return r.status, data


def lst(cookie, **q):
    s, d = req("GET", "/api/notifications?" + urllib.parse.urlencode(q), cookie)
    return d if s == 200 else {"items": [], "unread": -1}


def cleanup():
    with connect() as c, c:
        c.execute("DELETE FROM notification WHERE uid LIKE 'gb-%' OR actor_uid LIKE 'gb-%'")
        c.execute("DELETE FROM chat WHERE created_by LIKE 'gb-%'")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'gb-%' OR target LIKE 'gb-%'")
        c.execute("DELETE FROM note_comment WHERE owner_uid LIKE 'gb-%' OR uid LIKE 'gb-%'")
        c.execute("DELETE FROM note_comment_seen WHERE uid LIKE 'gb-%'")
        c.execute("DELETE FROM note WHERE uid LIKE 'gb-%'")
        c.execute("DELETE FROM user_kv WHERE uid LIKE 'gb-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'gb-%'")


def chat(kind, members, name=None):
    now = time.time()
    with connect() as c, c:
        dk = "|".join(sorted(members)) if kind == "dm" else None
        cid = c.execute("INSERT INTO chat (kind, dm_key, group_name, created_by, created) VALUES (?,?,?,?,?)",
                        (kind, dk, name, members[0], now)).lastrowid
        for u in members:
            c.execute("INSERT INTO chat_member (chat_id, uid, joined) VALUES (?,?,?)", (cid, u, now))
    return cid


def msg(to, sender, cid, fresh=False):
    """Neue Chat-Nachricht wie saga.chat_prefs.on_message (innerhalb 10 min gebuendelt); fresh: als waere die letzte
    Buendelung schon gelesen/aelter (neue Benachrichtigung statt fortschreiben)."""
    with connect() as c, c:
        if fresh:
            c.execute("UPDATE notification SET created=created-700 WHERE uid=? AND kind='chat.message'", (to,))
        return notify.create(c, to, "chat.message", sender, data={"chat": cid}, bundle=cid)


def missed(to, caller, cid, media="video"):
    now = time.time()
    with connect() as c, c:
        call = "gb" + os.urandom(8).hex()
        c.execute("INSERT INTO chat_call (id, chat_id, caller, callee, caller_device, media, state, reason, created, ended) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?)", (call, cid, caller, to, "dev", media, "ended", "timeout", now, now))
        return notify.create(c, to, "call.missed", caller, data={"chat": cid, "call": call})


def main():
    cleanup()
    prev = auth.settings(fresh=True)
    prev_en = prev.get("share_enabled", True)
    auth.save_setting("share_enabled", True)
    for u, n in (("gb-a", "Anna Glocke"), ("gb-b", "Bert Glocke"), ("gb-c", "Cora Glocke"), ("gb-d", "Dora Glocke")):
        auth.create_user(u, PW, n)
    A, Bk = ck("gb-a"), ck("gb-b")

    # --- Chat: dreimal Bert, einmal Cora (je mit Pause, also einzelne Benachrichtigungen) ------------------------------
    dm_b, dm_c = chat("dm", ["gb-a", "gb-b"]), chat("dm", ["gb-a", "gb-c"])
    grp = chat("group", ["gb-a", "gb-b", "gb-c", "gb-d"], "gb-team")
    msg("gb-a", "gb-b", dm_b)
    msg("gb-a", "gb-b", dm_b)                   # fortgeschrieben (n=2, wie bisher)
    msg("gb-a", "gb-b", dm_b, fresh=True)        # neue Benachrichtigung, gleicher Chat
    msg("gb-a", "gb-c", dm_c, fresh=True)
    msg("gb-a", "gb-b", dm_b, fresh=True)        # Bert zuletzt
    old = lst(A)
    chats_old = [x for x in old["items"] if x["kind"] == "chat.message"]
    ok(f"ohne ?group=1: einzeln wie bisher ({len(chats_old)} Zeilen, unread {old['unread']}, kein unread_groups)",
       len(chats_old) == 4 and old["unread"] == 4 and "unread_groups" not in old)
    ok("ohne ?group=1: group_key je Eintrag (neues Feld), alte Felder da",
       all(x.get("group_key") == f"chat:{dm_b if x['title'] == 'Bert Glocke' else dm_c}" for x in chats_old)
       and all(k in chats_old[0] for k in ("id", "kind", "created", "read", "actor", "count", "access", "note", "url", "text", "title")))
    d = lst(A, group=1)
    rows = [x for x in d["items"] if x["kind"] == "chat.message"]
    rb = [x for x in rows if x["title"] == "Bert Glocke"]
    rc = [x for x in rows if x["title"] == "Cora Glocke"]
    ok(f"gruppiert: zwei Personen -> zwei Zeilen ({[(x['title'], x['count']) for x in rows]})", len(rows) == 2 and len(rb) == 1 and len(rc) == 1)
    rb = rb[0] if rb else {}
    ok(f"Bert: eine Zeile, 4 Nachrichten aus 3 Mitteilungen (count {rb.get('count')}, ids {rb.get('ids')}, counts {rb.get('counts')})",
       rb.get("count") == 4 and len(rb.get("ids") or []) == 3 and sum(rb.get("counts") or []) == 4 and rb.get("ids") == sorted(rb["ids"], reverse=True))
    ok(f"... Kopf = neueste (id, Zeit), first = aelteste ({rb.get('id')}, {rb.get('first')} < {rb.get('created')})",
       rb.get("id") == (rb.get("ids") or [0])[0] and rb.get("first", 0) < rb.get("created", 0))
    ok(f"... Satz '{rb.get('text')}', Zeile oben (neueste)", rb.get("text") == "4 neue Nachrichten" and d["items"][0].get("id") == rb.get("id"))
    ok(f"... Cora einzeln mit altem Satz ({rc[0]['text'] if rc else None}, ids {rc[0].get('ids') if rc else None})",
       rc and rc[0]["text"] == "Neue Nachricht von Cora Glocke" and rc[0]["ids"] == [rc[0]["id"]])
    ok(f"Zaehler: unread {d['unread']} Mitteilungen, unread_groups {d.get('unread_groups')} Zeilen", d["unread"] == 4 and d.get("unread_groups") == 2)
    ok("limit=0&group=1: nur Zahlen mit unread_groups", lst(A, limit=0, group=1).get("unread_groups") == 2)

    # --- Gruppen-Chat: "(von A, B)" ----------------------------------------------------------------------------
    msg("gb-a", "gb-b", grp, fresh=True)
    msg("gb-a", "gb-c", grp)
    msg("gb-a", "gb-d", grp, fresh=True)
    g = [x for x in lst(A, group=1)["items"] if x.get("group_key") == f"chat:{grp}"]
    ok(f"Gruppen-Chat: eine Zeile '{g[0]['text'] if g else None}' ({g[0].get('actors') if g else None})",
       len(g) == 1 and g[0]["title"] == "gb-team" and g[0]["count"] == 3
       and g[0]["text"] == "3 neue Nachrichten (von Dora Glocke, Cora Glocke, Bert Glocke)")

    # --- Gelesen per Gruppe ---------------------------------------------------------------------------------------
    s, r = req("POST", "/api/notifications/read", A, {"groups": [f"chat:{dm_b}"]})
    ok(f"Gelesen {{groups: [chat:Bert]}} ({s}, unread {r.get('unread')}, unread_groups {r.get('unread_groups')})",
       s == 200 and r.get("unread") == 3 and r.get("unread_groups") == 2)
    d = lst(A, group=1, unread=1)
    ok("... Bert-Zeile nicht mehr ungelesen, Cora und Gruppe schon",
       {x["group_key"] for x in d["items"]} == {f"chat:{dm_c}", f"chat:{grp}"})
    rr = [x for x in lst(A, group=1)["items"] if x.get("group_key") == f"chat:{dm_b}"]
    ok(f"... gelesene Bert-Mitteilungen weiter EINE Zeile (read) ({[(x['read'], x['count']) for x in rr]})", len(rr) == 1 and rr[0]["read"] and rr[0]["count"] == 4)
    msg("gb-a", "gb-b", dm_b, fresh=True)
    rr = [x for x in lst(A, group=1)["items"] if x.get("group_key") == f"chat:{dm_b}"]
    ok(f"... neue Nachricht danach: ungelesene Zeile + gelesene Zeile ({[(x['read'], x['count']) for x in rr]})",
       [(x["read"], x["count"]) for x in rr] == [(False, 1), (True, 4)])
    s, r = req("POST", "/api/notifications/read", A, {"ids": rr[0]["ids"], "groups": [f"chat:{grp}"]})
    ok(f"Gelesen ids + groups kombiniert (unread {r.get('unread')}, Zeilen {r.get('unread_groups')})", s == 200 and r.get("unread") == 1 and r.get("unread_groups") == 1)
    ok("Gelesen: ungueltige groups -> 400", req("POST", "/api/notifications/read", A, {"groups": "chat:1"})[0] == 400
       and req("POST", "/api/notifications/read", A, {"groups": [5]})[0] == 400)
    ok("Gelesen: fremde Gruppen aendern nichts bei anderen", req("POST", "/api/notifications/read", Bk, {"groups": [f"chat:{dm_c}"]})[0] == 200
       and lst(A, limit=0, group=1)["unread_groups"] == 1)
    req("POST", "/api/notifications/read", A, {"all": True})

    # --- Verpasste Anrufe -------------------------------------------------------------------------------------------
    for _ in range(5):
        missed("gb-a", "gb-b", dm_b)
    missed("gb-a", "gb-c", dm_c, media="audio")
    missed("gb-a", "gb-c", dm_c, media="video")
    d = lst(A, group=1)
    cb = [x for x in d["items"] if x.get("group_key") == f"call:{dm_b}"]
    cc = [x for x in d["items"] if x.get("group_key") == f"call:{dm_c}"]
    ok(f"Anrufe: 5 verpasste von Bert -> eine Zeile '{cb[0]['text'] if cb else None}' (media {cb[0].get('media') if cb else None})",
       len(cb) == 1 and cb[0]["count"] == 5 and cb[0]["text"] == "5 verpasste Videoanrufe" and cb[0]["media"] == "video"
       and cb[0]["chat"]["kind"] == "dm" and cb[0]["url"] == f"/chats#c{dm_b}")
    ok(f"... gemischt Ton/Video -> 'Anrufe' ({cc[0]['text'] if cc else None})", len(cc) == 1 and cc[0]["text"] == "2 verpasste Anrufe")
    ok(f"... unread_groups {d.get('unread_groups')} (2 Zeilen), unread {d['unread']} (7)", d.get("unread_groups") == 2 and d["unread"] == 7)
    single = [x for x in lst(A)["items"] if x["kind"] == "call.missed"][0]
    ok(f"... ohne Buendeln: alter Satz + media ({single['text']}, {single.get('media')})", single["text"].startswith("Verpasster Anruf von") and single.get("media") in ("audio", "video"))
    req("POST", "/api/notifications/read", A, {"all": True})

    # --- Kommentare auf derselben Seite ---------------------------------------------------------------------------
    s, pg = req("POST", "/api/notes", A, {"title": "Glocke Seite", "icon": "🔔"})
    pg = pg["id"]
    s2, pg2 = req("POST", "/api/notes", A, {"title": "Andere Seite"})
    pg2 = pg2["id"]
    for p in (pg, pg2):
        req("PUT", f"/api/notes/{p}/shares", A, {"type": "user", "target": "gb-b", "role": "comment"})
    req("POST", "/api/notifications/read", A, {"all": True})
    for i in range(3):                               # drei Unterhaltungen auf derselben Seite (je eine Benachrichtigung)
        s, _ = req("POST", f"/api/notes/{pg}/comments", Bk, {"block_id": f"b{i}", "body": f"Kommentar {i}"})
    req("POST", f"/api/notes/{pg2}/comments", Bk, {"block_id": "b0", "body": "Woanders"})
    d = lst(A, group=1)
    pc = [x for x in d["items"] if x.get("group_key") == f"page:{pg}"]
    po = [x for x in d["items"] if x.get("group_key") == f"page:{pg2}"]
    ok(f"Kommentare: 3 Unterhaltungen auf einer Seite -> eine Zeile '{pc[0]['text'] if pc else None}' ({pc[0].get('title') if pc else None})",
       len(pc) == 1 and pc[0]["count"] == 3 and pc[0]["text"] == "3 neue Kommentare" and pc[0]["title"] == "Glocke Seite"
       and len(pc[0]["ids"]) == 3 and pc[0]["excerpt"] == "Kommentar 2")
    ok(f"... andere Seite eigene Zeile ({po[0]['text'] if po else None})", len(po) == 1 and po[0]["text"] == "Bert Glocke hat deine Seite kommentiert")
    s, r = req("POST", "/api/notifications/read", A, {"ids": pc[0]["ids"] if pc else []})
    ok(f"... Zeile gelesen (alle ids) -> nur noch die andere Seite ({r.get('unread_groups')})", r.get("unread_groups") == 1 and r.get("unread") == 1)
    req("POST", "/api/notifications/read", A, {"all": True})

    # --- Andere Arten unveraendert ----------------------------------------------------------------------------------
    with connect() as c, c:
        notify.create(c, "gb-a", "page.mention", "gb-b", pg, data={"block": "b1"})
        notify.create(c, "gb-a", "page.mention", "gb-c", pg2, data={"block": "b1"})
    pm = [x for x in lst(A, group=1)["items"] if x["kind"] == "page.mention"]
    ok(f"Seiten-Erwaehnungen bleiben einzeln ({len(pm)}, group_key {[x.get('group_key') for x in pm]})",
       len(pm) == 2 and all(x["group_key"] is None and x["ids"] == [x["id"]] for x in pm))
    sh = [x for x in lst(A, group=1)["items"] if x["kind"].startswith("share.")]
    ok("Freigaben einzeln (kein group_key)", all(x.get("group_key") is None for x in sh))

    # --- Blaettern: limit zaehlt Zeilen, nichts doppelt/verloren ----------------------------------------------------
    req("POST", "/api/notifications/read", A, {"all": True})
    for i in range(4):                               # abwechselnd: Bert, Cora, Bert, Cora ... (Einzel-Mitteilungen)
        msg("gb-a", "gb-b", dm_b, fresh=True)
        msg("gb-a", "gb-c", dm_c, fresh=True)
    full = lst(A, group=1, unread=1, limit=100)
    ok(f"ungelesen: 2 Zeilen aus 8 Mitteilungen ({[(x['title'], x['count']) for x in full['items']]})",
       len(full["items"]) == 2 and sum(len(x["ids"]) for x in full["items"]) == 8)
    p1 = lst(A, group=1, unread=1, limit=1)
    p2 = lst(A, group=1, unread=1, limit=1, before=p1.get("next") or 0)
    seen = [i for p in (p1, p2) for x in p["items"] for i in x["ids"]]
    all_ids = sorted(i for x in full["items"] for i in x["ids"])
    ok(f"limit=1: Seite 1 eine Zeile, more, next ({len(p1['items'])}, {p1['more']}, {p1['next']})", len(p1["items"]) == 1 and p1["more"] and p1["next"])
    rest = p2
    while rest.get("more") and rest.get("next"):
        rest = lst(A, group=1, unread=1, limit=1, before=rest["next"])
        seen += [i for x in rest["items"] for i in x["ids"]]
    ok(f"... alle Seiten zusammen: jede Mitteilung genau einmal ({len(seen)} von {len(all_ids)})", sorted(seen) == all_ids)
    ok("... Seite 1 ohne Mitglieder unterhalb der Grenze", all(i >= (p1["next"] or 0) for x in p1["items"] for i in x["ids"]))

    auth.save_setting("share_enabled", prev_en)
    cleanup()
    print(f"\nMITTEILUNGEN-BUENDELN {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        cleanup()
        raise
