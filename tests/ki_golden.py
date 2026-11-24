"""Golden-Vergleich der Modell-Nachrichten (Saga 2.6 K-A4, Bugs-DB 2266, Konzept K8): feste Faelle fuer alle KI-Wege
(Frage, Frage mit Aufgaben/Kalender, Frage einer anderen Person mit geteilter Seite, Seiten-Hilfen, Zuordnen in einer
Datenbank, Chat-Zuruf mit Verlauf) gegen das Fake-Modell (tests/fakeai.py). Erfasst werden die Nachrichten an das Modell
(mit fester Rahmen-Kennung "RID", festem Datum/Uhrzeit-Satz und Seiten-IDs als Platzhalter), das Ereignis 'sources' und
'done' (ohne das neue Feld 'read' und ohne zufaellige Belege). Der Umbau auf saga/ai_ctx.py darf daran nichts aendern.

Aufruf (saga-test): tools/testumgebung/umgebung.sh lauf server <slot> tests/ki_golden.py
  KI_GOLDEN_WRITE=1  schreibt die Vorlage (Stand vor dem Umbau) als JSON in das Log (Zeile 'GOLDEN-JSON ...') und nach
                     $KI_GOLDEN_OUT (Standard: tests/vectors/ki_golden.json)
  sonst              vergleicht mit tests/vectors/ki_golden.json
Wegwerf-Konten kg-*; raeumt auf."""
import json, os, re, socket, subprocess, sys, time
import auth
from db import connect

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chat as CT  # noqa: E402
import ki as K  # noqa: E402

GOLD = os.path.join(K.HERE, "vectors", "ki_golden.json")
T0 = 1793530800.0                    # feste Aenderungszeit der Seiten (2026-11-01)
IDS = {}                             # Seiten-ID -> Platzhalter


def ok(n, c, info=""):
    K.ok(n if c else f"{n} {str(info)[:600]}", c)


def pid(name, nid):
    IDS[nid] = name
    return nid


def norm_text(s):
    s = re.sub(r"Today is [^\n]*\. Use this", "Today is X. Use this", s)
    m = re.search(r"<<<SAGA_DATA ([0-9a-f]{12})", s)
    if m:
        s = s.replace(m.group(1), "RID")
    for nid, name in sorted(IDS.items(), key=lambda x: -len(str(x[0]))):
        s = re.sub(rf"(?<=#){nid}(?!\d)", name, s)
    return s


def norm_src(srcs):
    return [dict(x, id=IDS.get(x.get("id"), x.get("id"))) for x in srcs or []]


def norm_done(d):
    d = dict(d or {})
    d.pop("read", None)                              # neu mit K-A4 (nur Kennungen) - nicht Teil der Vorlage
    if "history_id" in d:
        d["history_id"] = "H"
    if isinstance(d.get("reply"), dict):
        d["reply"] = {"id": "R", "sha256": d["reply"].get("sha256")}
    if isinstance(d.get("assign"), dict):
        a = dict(d["assign"])
        a["db"] = IDS.get(a.get("db"), a.get("db"))
        a["items"] = [dict(it, id=IDS.get(it.get("id"), it.get("id"))) for it in a.get("items") or []]
        d["assign"] = a
    return d


def capture(name, st, ev):
    body = K.lastbody()
    msgs = [{"role": m["role"], "content": norm_text(m["content"])} for m in body.get("messages") or []]
    rec = {"status": st, "messages": msgs, "max_tokens": body.get("max_tokens"),
           "sources": norm_src((K.get(ev, "sources") or {}).get("sources")) if isinstance(ev, list) else None,
           "done": norm_done(K.get(ev, "done")) if isinstance(ev, list) else ev,
           "text": K.text_of(ev)}
    K.fake("POST", "/_reset")
    return name, rec


def fix_times(uid):
    with connect() as c, c:
        c.execute("UPDATE note SET updated=?, created=? WHERE uid=?", (T0, T0, uid))


def cases():
    out = []
    A, Bk = K.ck("kg-a"), K.ck("kg-b")
    p_budget = pid("P_BUDGET", K.page(A, "Zitronenfalter Budget", "Das Budget fuer Zitronenfalter betraegt 4711 Euro.",
                                      "Zweiter Absatz mit Zitronenfalter Details."))
    p_geheim = pid("P_GEHEIM", K.page(A, "Geheimtitel Quokka", "Nur fuer mich."))
    p_meet = pid("P_MEET", K.page(A, "Zitronenfalter Meeting", "Das Zitronenfalter Meeting ist am Montag.",
                                  f'Siehe <a href="/notes#{p_geheim}">Geheimtitel Quokka</a> zum Zitronenfalter.'))
    pid("P_BERT", K.page(Bk, "Notizen von Bert", "Zitronenfalter sind gelb."))
    p_arch = pid("P_ARCH", K.page(A, "Zitronenfalter Archiv", "Zitronenfalter 999 im Archiv."))
    p_trash = pid("P_TRASH", K.page(A, "Zitronenfalter Papierkorb", "Zitronenfalter 888 im Papierkorb."))
    K.req("POST", f"/api/notes/{p_arch}/archive", A, {"archived": True})
    K.req("DELETE", f"/api/notes/{p_trash}", A)
    K.req("PUT", f"/api/notes/{p_meet}/shares", A, {"type": "user", "target": "kg-b", "role": "view"})
    for txt, due, prio in (("Zitronenfalter fuettern", "2026-12-01", 1), ("Steuer machen", None, 2)):
        s, d = K.req("POST", "/api/tasks", A, {"text": txt, **({"due": due} if due else {}), "prio": prio})
    # Datenbank mit Eintraegen auf einer Seite (Zuordnen)
    edb = pid("P_DB", K.req("POST", "/api/notes", A, {"title": "Aufgabenliste Falter", "kind": "database"})[1]["id"])
    esch = {"props": [{"id": "prio", "name": "Prio", "type": "select", "options": [{"id": "ph", "name": "Hoch", "color": "red"},
                                                                                       {"id": "pn", "name": "Niedrig", "color": "gray"}]},
                      {"id": "nt", "name": "Notiz", "type": "text"}]}
    K.req("PATCH", f"/api/notes/{edb}", A, {"schema": esch})
    for i, (title, props) in enumerate((("Server brennt dringend", {}), ("Kaffee kaufen", {"nt": "Bohnen"}),
                                        ("Schon erledigt dringend", {"prio": "pn"}))):
        pid(f"P_ROW{i}", K.req("POST", "/api/notes", A, {"title": title, "parent_id": edb, "props": props})[1]["id"])
    hp = pid("P_HOME", K.page(A, "Projektseite Falter", "Hier sammeln wir alles zum Umzug."))
    K.req("PATCH", f"/api/notes/{hp}", A, {"format": "blocks", "body": json.dumps({"blocks": [
        {"id": "x1", "type": "paragraph", "data": {"text": "Hier sammeln wir alles zum Umzug."}},
        {"id": "x2", "type": "database", "data": {"db": edb}}]})})
    fix_times("kg-a")
    fix_times("kg-b")
    K.fake("POST", "/_reset")

    # --- Frage ---------------------------------------------------------------------------------------------------------
    st, ev, _ = K.ask(A, "Was weiss ich ueber Zitronenfalter?")
    out.append(capture("ask_pages", st, ev))
    st, ev, _ = K.ask(A, "Was ist mit dem Zitronenfalter Budget?", tasks=True, calendar=True)
    out.append(capture("ask_tasks_cal", st, ev))
    st, ev, _ = K.ask(A, "Welche Aufgaben habe ich offen?", tasks=True)
    out.append(capture("ask_tasks_only", st, ev))
    st, ev, _ = K.ask(Bk, "Was weiss ich ueber Zitronenfalter?")
    out.append(capture("ask_b_shared", st, ev))
    st, ev, _ = K.ask(A, "Wombat Xylophon Quasar")
    out.append(capture("ask_no_match", st, ev))
    K.req("PUT", "/api/ai/settings", A, {"history": True})
    st, ev, _ = K.ask(A, "Wann ist das Zitronenfalter Meeting?")
    out.append(capture("ask_history_on", st, ev))
    K.req("PUT", "/api/ai/settings", A, {"history": False})

    # --- Seite ---------------------------------------------------------------------------------------------------------
    for act, extra in (("summarize", {}), ("tasks", {}), ("improve", {"blocks": ["b0"]}), ("translate", {"blocks": ["b0", "b1"], "target": "en"})):
        st, ev, _ = K.sse(f"/api/ai/page/{p_budget}", A, dict(action=act, **extra))
        out.append(capture(f"page_{act}", st, ev))
    st, ev, _ = K.sse(f"/api/ai/page/{p_meet}", Bk, {"action": "summarize"})
    out.append(capture("page_b_shared_masked", st, ev))
    st, ev, _ = K.sse(f"/api/ai/page/{hp}", A, {"action": "summarize"})
    out.append(capture("page_embedded_db", st, ev))
    st, ev, _ = K.sse(f"/api/ai/page/{edb}", A, {"action": "summarize"})
    out.append(capture("page_database", st, ev))
    st, ev, _ = K.sse(f"/api/ai/page/{hp}", A, {"action": "assign", "db": edb, "prop": "prio"})
    out.append(capture("page_assign", st, ev))
    st, ev, _ = K.sse(f"/api/ai/page/{p_trash}", A, {"action": "summarize"})
    out.append(capture("page_trash_404", st, ev))

    # --- Chat-Zuruf ----------------------------------------------------------------------------------------------------
    Ca, Cb = CT.Person("kg-a"), CT.Person("kg-b")
    dm = CT.req("POST", "/api/chats", Ca.k, {"kind": "dm", "user": "kg-b"})[1]["chat"]["id"]
    CT.req("PUT", f"/api/chats/{dm}/ai", Cb.k, {"allowed": True})
    hist = [{"name": "Bert KG", "text": "Wir planen die Reise zum Zitronenfalter."}, {"name": "Anna KG", "text": "Abflug am Montag"}]
    st, ev, _ = K.sse(f"/api/chats/{dm}/ai", Ca.k, {"q": "@Marin wie spaet ist es in Tokio?", "history": hist})
    out.append(capture("chat_mention", st, ev))
    st, ev, _ = K.sse(f"/api/chats/{dm}/ai", Ca.k, {"q": "@Marin hallo ohne Verlauf"})
    out.append(capture("chat_no_history", st, ev))
    return dict(out)


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'kg-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM chat WHERE dm_key LIKE '%kg-%' OR created_by LIKE 'kg-%' "
                  "OR id IN (SELECT chat_id FROM chat_member WHERE uid LIKE 'kg-%')")
        for t in ("note", "user_kv", "task", "ai_history", "ai_usage", "notification", "chat_identity", "chat_device", "chat_ai_reply"):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE 'kg-%'")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'kg-%' OR target LIKE 'kg-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'kg-%'")


def main():
    cleanup()
    prev = auth.settings(fresh=True)
    pv = dict(ai=dict(prev["ai"]), persona=dict(prev.get("ai_persona") or {}), chat=prev.get("chat_enabled", True),
              share=prev.get("share_enabled", True))
    auth.save_setting("chat_enabled", True)
    auth.save_setting("share_enabled", True)
    auth.save_setting("ai_persona", {"name": "Marin", "tone": ""})
    auth.save_setting("ai", {"enabled": True, "provider": "openai", "url": K.FAKE + "/v1", "model": "fake", "api_key": "", "groups": [],
                             "timeout": 10, "quota": 0, "context": 8000, "history": True, "pinned": ["127.0.0.1"], "chat": True,
                             "web": False})
    K.key_raw("")
    proc = subprocess.Popen([sys.executable, os.path.join(K.HERE, "fakeai.py"), str(K.PORT)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", K.PORT), timeout=0.2).close(); break
            except OSError:
                time.sleep(0.1)
        auth.create_user("kg-a", CT.PW, "Anna KG")
        auth.create_user("kg-b", CT.PW, "Bert KG")
        time.sleep(K.WAIT)
        got = cases()
        ok(f"Faelle erfasst ({len(got)})", len(got) == 17, sorted(got))
        ok("jeder Modell-Fall hat Nachrichten mit fester Kennung", all(
            "RID" in v["messages"][0]["content"] for k, v in got.items() if v["messages"]), [k for k, v in got.items() if not v["messages"]])
        if os.environ.get("KI_GOLDEN_WRITE"):
            raw = json.dumps(got, ensure_ascii=False, sort_keys=True, indent=1)
            dst = os.environ.get("KI_GOLDEN_OUT") or GOLD
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "w", encoding="utf-8") as f:
                f.write(raw + "\n")
            print("GOLDEN-JSON", dst, len(raw), flush=True)
        else:
            with open(GOLD, encoding="utf-8") as f:
                want = json.load(f)
            ok("gleiche Faelle wie die Vorlage", sorted(want) == sorted(got), (sorted(want), sorted(got)))
            for k in sorted(want):
                g, w = got.get(k), want[k]
                for part in ("status", "messages", "max_tokens", "sources", "done", "text"):
                    same = (g or {}).get(part) == w.get(part)
                    ok(f"{k}: {part} unveraendert", same, f"\nvorher: {json.dumps(w.get(part), ensure_ascii=False)[:1500]}"
                                                         f"\nnachher: {json.dumps((g or {}).get(part), ensure_ascii=False)[:1500]}")
    finally:
        proc.terminate()
        for s in CT.STREAMS:
            s.close()
        auth.save_setting("ai", pv["ai"])
        K.key_raw(pv["ai"].get("api_key") or "")
        auth.save_setting("ai_persona", pv["persona"])
        auth.save_setting("chat_enabled", pv["chat"])
        auth.save_setting("share_enabled", pv["share"])
        cleanup()
    print(f"\nKI-Golden {sum(K.res)}/{len(K.res)}", "- ALLES GRUEN" if all(K.res) else "- FEHLER")
    sys.exit(0 if all(K.res) else 1)


if __name__ == "__main__":
    main()
