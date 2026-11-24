"""Erste Schritte (2.6, Bugs-DB 2031) gegen einen laufenden Server: Frage nur fuer neue Konten ohne eigene Seiten, Ja legt
die Seite mit Haekchenliste an, Nein legt nichts an, Abhaken nur von Neuem und je Punkt einmal, Seite geloescht -> neu
anlegbar (Erledigtes gleich abgehakt), Admin-Schalter, Chats aus -> kein Chat-Punkt. Wegwerf-Konten fs-*.
Aufruf (saga-test, Server wie bei tests/ki.py): python tests/erste_schritte.py"""
import json
import sys
import time

import auth
from db import connect
import ki as K  # noqa: E402  (req, ck, WAIT)

ok = K.ok


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'fs-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        for tb in ("note", "user_kv", "task", "chat_identity", "device_login"):
            c.execute(f"DELETE FROM {tb} WHERE uid LIKE 'fs-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'fs-%'")


def checklist(cookie, nid):
    s, d = K.req("GET", f"/api/notes/{nid}", cookie)
    body = json.loads(d.get("body") or "{}") if isinstance(d, dict) else {}
    out = []
    for b in body.get("blocks") or []:
        if b.get("type") == "list" and (b.get("data") or {}).get("style") == "checklist":
            out += [(it.get("content"), bool((it.get("meta") or {}).get("checked"))) for it in b["data"].get("items") or []]
    return s, d, out


def get(cookie):
    return K.req("GET", "/api/first-steps", cookie)


def main():
    cleanup()
    prev = auth.settings(fresh=True)
    pv = {"first_steps": prev.get("first_steps", True), "chat_enabled": prev.get("chat_enabled", True)}
    auth.save_setting("first_steps", True); auth.save_setting("chat_enabled", True)
    time.sleep(K.WAIT)
    try:
        run()
    finally:
        for k, v in pv.items():
            auth.save_setting(k, v)
        cleanup()
    print(f"\nErste Schritte {sum(K.res)}/{len(K.res)}", "- ALLES GRUEN" if all(K.res) else "- FEHLER")
    sys.exit(0 if all(K.res) else 1)


def run():
    auth.create_user("fs-a", K.PW, "Frida Start")
    auth.create_user("fs-b", K.PW, "Bodo Bestand")
    A, B = K.ck("fs-a"), K.ck("fs-b")

    s, d = K.req("GET", "/api/first-steps")
    ok(f"ohne Anmeldung: 401 ({s})", s == 401)
    s, d = get(A)
    ok(f"neues Konto: fragen ({s}, {d})", s == 200 and d["enabled"] is True and d["ask"] is True and d["state"] is None
       and d["page"] is None)
    ok(f"Punkte in fester Reihenfolge, nichts erledigt ({d.get('items')})", [x["key"] for x in d["items"]] == ["avatar", "chat", "app", "page", "task"]
       and not any(x["done"] for x in d["items"]))

    nb = K.req("POST", "/api/notes", B, {"title": "Bodos Notizen"})[1]["id"]
    s, d = get(B)
    ok(f"Konto mit eigenen Seiten (nach dem Update): nicht fragen ({d.get('ask')})", d["ask"] is False and d["state"] is None)
    bad = [K.req("POST", "/api/first-steps", B, b)[0] for b in ({"answer": "vielleicht"}, {}, {"answer": True})]
    ok(f"ungueltige Antworten -> 400 ({bad})", bad == [400, 400, 400])
    s, d = K.req("POST", "/api/first-steps", B, {"answer": "no"})
    s2, d2 = get(B)
    with connect() as c:
        n = c.execute("SELECT COUNT(*) FROM note WHERE uid='fs-b' AND deleted_at IS NULL").fetchone()[0]
    ok(f"Nein: nichts angelegt, Zustand no, keine Frage mehr ({s}, {d2.get('state')}, {n})", s == 200 and d2["state"] == "no"
       and d2["ask"] is False and d2["page"] is None and n == 1)

    s, d = K.req("POST", "/api/first-steps", A, {"answer": "yes"})
    pid = (d or {}).get("page")
    ok(f"Ja: Seite angelegt ({s}, {pid})", s == 200 and isinstance(pid, int))
    s, pg, items = checklist(A, pid)
    ok(f"Seite 'Erste Schritte' mit 5 offenen Punkten ({pg.get('title')}, {len(items)})", s == 200 and pg.get("title") == "Erste Schritte"
       and len(items) == 5 and not any(x[1] for x in items) and "Profilbild" in items[0][0] and "Aufgabe" in items[4][0])
    s, d = get(A)
    ok(f"danach: Zustand yes, Seite gemerkt, keine Frage ({d.get('state')}, {d.get('page')})", d["state"] == "yes" and d["page"] == pid
       and d["ask"] is False)
    ok("die Einfuehrungsseite selbst zaehlt nicht als 'eigene Seite'", not [x for x in d["items"] if x["key"] == "page"][0]["done"])

    K.req("POST", "/api/tasks", A, {"text": "Milch kaufen"})
    s, d = get(A)
    _, _, items = checklist(A, pid)
    ok(f"Aufgabe angelegt: Punkt erledigt und abgehakt ({items[4]})", [x for x in d["items"] if x["key"] == "task"][0]["done"] and items[4][1]
       and not any(x[1] for x in items[:4]))
    sub = K.req("POST", "/api/notes", A, {"title": "Unterseite der Einfuehrung", "parent_id": pid})[1]["id"]
    s, d = get(A)
    ok("Unterseite der Einfuehrung zaehlt nicht", not [x for x in d["items"] if x["key"] == "page"][0]["done"])
    K.req("POST", "/api/notes", A, {"title": "Fridas Rezepte"})
    with connect() as c, c:
        c.execute("INSERT INTO chat_identity (uid, ident_pub, backup_pub, backup_sig, bundle, created, updated) VALUES "
                  "('fs-a', 'x', 'x', 'x', '{}', ?, ?)", (time.time(), time.time()))
        c.execute("INSERT INTO device_login (uid, uname, name, hash, prefix, created, last_used) VALUES "
                  "('fs-a', 'fs-a', 'iPhone', 'fs-test-hash', 'fs', ?, ?)", (time.time(), time.time()))
    s, d = get(A)
    _, _, items = checklist(A, pid)
    ok(f"Seite, Chat und App: abgehakt, Profilbild offen ({[x[1] for x in items]})", [x[1] for x in items] == [False, True, True, True, True])

    # Nutzerin hakt die Aufgabe selbst wieder ab -> bleibt so (jeder Punkt nur einmal)
    s, pg, _ = checklist(A, pid)
    body = json.loads(pg["body"])
    for b in body["blocks"]:
        if b.get("type") == "list":
            b["data"]["items"][4]["meta"]["checked"] = False
    s, _ = K.req("PATCH", f"/api/notes/{pid}", A, {"body": json.dumps(body), "format": "blocks", "rev": pg["rev"]})
    get(A)
    _, _, items = checklist(A, pid)
    ok(f"selbst wieder abgehakt: Saga hakt nicht erneut ab ({s}, {items[4]})", s == 200 and items[4][1] is False)
    # Text einer Zeile geaendert -> keine Fehler, Zeile bleibt
    with connect() as c, c:
        c.execute("DELETE FROM chat_identity WHERE uid='fs-a'")
    s, d = get(A)
    ok(f"Zustand ueberlebt Aenderungen am Konto ({s})", s == 200)

    # Seite in den Papierkorb -> page None; Ja legt sie neu an, Erledigtes gleich abgehakt
    K.req("DELETE", f"/api/notes/{pid}", A)
    s, d = get(A)
    ok(f"Seite geloescht: page None, Zustand bleibt yes ({d.get('page')}, {d.get('state')})", d["page"] is None and d["state"] == "yes")
    s, d = K.req("POST", "/api/first-steps", A, {"answer": "yes"})
    pid2 = (d or {}).get("page")
    _, _, items = checklist(A, pid2)
    ok(f"neu angelegt, Erledigtes sofort abgehakt ({pid2}, {[x[1] for x in items]})", isinstance(pid2, int) and pid2 != pid
       and [x[1] for x in items] == [False, False, True, True, True])
    s, d = K.req("POST", "/api/first-steps", A, {"answer": "yes"})
    ok(f"noch einmal Ja: dieselbe Seite, keine zweite ({d.get('page')})", d.get("page") == pid2)
    s, d = K.req("POST", "/api/first-steps", A, {"answer": "no"})
    ok("Nein nach Ja: Seite und Zustand bleiben", get(A)[1]["state"] == "yes" and get(A)[1]["page"] == pid2)
    del sub

    # Pruefer 2: gleichzeitiges "Ja" (Web + App, zwei Tabs) -> genau eine Seite
    import threading
    auth.create_user("fs-d", K.PW, "Dora Doppelt")
    D = K.ck("fs-d")
    got = []
    th = [threading.Thread(target=lambda: got.append(K.req("POST", "/api/first-steps", D, {"answer": "yes"}))) for _ in range(4)]
    [x.start() for x in th]
    [x.join() for x in th]
    pages = {(d or {}).get("page") for s, d in got}
    with connect() as c:
        n = c.execute("SELECT COUNT(*) FROM note WHERE uid='fs-d' AND deleted_at IS NULL").fetchone()[0]
    ok(f"vier gleichzeitige Ja: eine Seite, alle bekommen dieselbe ({[s for s, _ in got]}, {pages}, {n})",
       all(s == 200 for s, _ in got) and len(pages) == 1 and n == 1)
    s, d = get(D)
    ok(f"danach: eigene Seite 'page' nicht durch eine verwaiste Doppelseite abgehakt ({d.get('items')})",
       not [x for x in d["items"] if x["key"] == "page"][0]["done"])

    # Chats aus -> kein Chat-Punkt; Admin-Schalter aus -> keine Frage, POST 403
    auth.save_setting("chat_enabled", False); time.sleep(K.WAIT)
    s, d = get(A)
    ok(f"Chats aus: kein Chat-Punkt ({[x['key'] for x in d['items']]})", [x["key"] for x in d["items"]] == ["avatar", "app", "page", "task"])
    auth.save_setting("chat_enabled", True)
    auth.save_setting("first_steps", False); time.sleep(K.WAIT)
    auth.create_user("fs-c", K.PW, "Cleo Neu")
    s, d = get(K.ck("fs-c"))
    ok(f"Admin aus: keine Frage, keine Punkte ({d})", d["enabled"] is False and d["ask"] is False and d["items"] == [])
    s, d = K.req("POST", "/api/first-steps", K.ck("fs-c"), {"answer": "yes"})
    ok(f"Admin aus: Ja -> 403 ({s})", s == 403)
    with connect() as c:
        n = c.execute("SELECT COUNT(*) FROM note WHERE uid='fs-c'").fetchone()[0]
    ok("Admin aus: nichts angelegt", n == 0)
    _, _, items = checklist(A, pid2)
    ok("Admin aus: vorhandene Seite bleibt", len(items) == 5)


if __name__ == "__main__":
    main()
