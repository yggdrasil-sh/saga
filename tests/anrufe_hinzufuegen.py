"""Anrufe 2.5, L8 "Personen hinzufuegen" (saga/call_add.py, POST /api/calls/{id}/add) - Server-Seite mit PyNaCl als Client.

Geprueft: Feature call_add; Anruf-Objekt can_add (Admin der freien Gruppe ja, Mitglied nein; Seiten-Chat: Eigentuemer mit
add_page = Seitentitel). Mitglied klingeln (auch durch ein einfaches Mitglied, wie /invite: n, invites, call.invite);
Aussenstehende: einfaches Mitglied -> 403 rights (nichts geaendert), Admin -> Mitglied (chat_member, chat.changed),
klingelt (call.invite, invites "ringing"), sieht den Anruf (joinable) und tritt bei (Angebote an alle Geraete); Handle
'~x@host' eines Kontos hier wie ein Benutzername; unbekannt bzw. gesperrt (block2) -> 404 mit Text, Mischung -> 200 mit
failed; entfernte Person (Gastgeber-Aktion) nur durch den Gastgeber (403 removed); Rate im selben Topf wie /invite (429);
direkter 1:1 -> 409 dm (move_block direct, L9); voll (Video, 4 Geraete, Zweitgeraet zaehlt) -> 409 full mit seats und Text, nichts geaendert; Seiten-Chat
ohne Bestaetigung 409 page_confirm (Titel, Name, nichts geteilt), mit confirm_page geteilt (Rolle Lesen) + Mitglied +
klingelt, Nicht-Eigentuemer 403; beendeter Anruf -> 409 ended. Keine Anmeldenamen anderer in Texten.
Wegwerf-Konten ca-a*; raeumt auf."""
import os
import secrets
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import anrufe as A_  # noqa: E402
import anrufe_raum as R_  # noqa: E402
import auth  # noqa: E402
from db import connect  # noqa: E402

ok, req, res = A_.ok, A_.req, A_.res
CAPS = R_.CAPS


def cfg(**kw):
    base = {"enabled": True, "stun": "stun:stun.example.org:3478", "turn_urls": "", "turn_mode": "rest", "turn_secret": "", "turn_user": "",
            "turn_password": "", "turn_ttl": 43200, "relay_only": False, "ring_s": 45, "horn": True}
    base.update(kw)
    auth.save_setting("calls", base)
    auth._settings_cache["v"] = None


USERS = (("ca-aa", "Anna Beispiel"), ("ca-ab", "Bert Beispiel"), ("ca-ac", "Cora Beispiel"), ("ca-ad", "Dora Neu"),
         ("ca-ae", "Emil Sperre"), ("ca-af", "Frieda Handle"), ("ca-ag", "Gustav Seite"), ("ca-ar", "Rita Rate"), ("ca-av", "Vera Voll"))


def main():
    A_.cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "share_group_scope", "calls")}
    auth.save_setting("chat_enabled", True)
    auth.save_setting("share_group_scope", False)
    cfg()
    for u, n in USERS:
        auth.create_user(u, A_.PW, n)
    time.sleep(A_.WAIT)
    try:
        run()
    finally:
        for x in A_.STREAMS:
            x.close()
        for k, v in keep.items():
            if v is not None:
                auth.save_setting(k, v)
        auth._settings_cache["v"] = None
        with connect() as c, c:
            c.execute("DELETE FROM fed_chat_block WHERE uid LIKE 'ca-a%'")
        A_.cleanup()
    print(f"\nANRUFE-HINZUFUEGEN {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


def st(lst):
    return {x["who"]: x["state"] for x in (lst or [])}


def get(p, cid):
    s, d = req("GET", f"/api/calls/{cid}", p.k)
    return d.get("call") or {}


def member(chat_id, uid):
    with connect() as c:
        return bool(c.execute("SELECT 1 FROM chat_member WHERE chat_id=? AND uid=? AND left_at IS NULL", (chat_id, uid)).fetchone())


def add(p, cid, uids, **kw):
    return req("POST", f"/api/calls/{cid}/add", p.k, dict({"uids": uids}, **kw))


def no_login_names(*texts):
    return not any(u in (x or "") for x in texts for u, _ in USERS)


def run():
    from saga import calls as CL, call_add as CA
    from saga import fed_calls, fed_chat                 # setzt CL.FED wie im Server
    assert CL.FED is fed_calls
    P = {u: A_.Person(u) for u, _ in USERS}
    A, B, Cc, D, E, F, G, Rr, V = (P[u] for u, _ in USERS)
    A2 = A.add_device()
    for p in P.values():
        for dv in p.devs:
            R_.caps_put(p, dv)
    s, cf = req("GET", "/api/calls/config", A.k)
    ok(f"Feature call_add in /api/calls/config ({s})", s == 200 and CA.FEATURE == "call_add" and "call_add" in cf.get("features", []))

    s, d = req("POST", "/api/chats", A.k, {"kind": "free", "members": ["ca-ab", "ca-ac"]})
    gid = d["chat"]["id"]
    sC, sD = A_.Stream(Cc.k), A_.Stream(D.k)
    time.sleep(0.5)
    g1 = secrets.token_hex(16)
    s, d = req("POST", "/api/calls", A.k, {"id": g1, "chat_id": gid, "media": "audio", "device": A.dev["id"], "caps": CAPS})
    call = d.get("call") or {}
    ok(f"Gruppenanruf (Mesh) gestartet, Anna (Admin) can_add ({s}, {call.get('can_add')}, {call.get('topo')})",
       s == 200 and call.get("can_add") is True and "add_page" not in call)
    s, d = R_.join(B, B.dev, g1, [(A, A.dev)])
    ok(f"Bert tritt bei ({s}), Bert (Mitglied) can_add false", s == 200 and d["call"].get("can_add") is False)

    # --- Mitglied klingeln (auch einfaches Mitglied) ------------------------------------------------------------------
    mC = sC.mark()
    s, d = add(B, g1, ["ca-ac"])
    ok(f"Bert holt Cora (Mitglied): wie /invite {{ok, n, invites, seats}} ({s}, n={d.get('n')}, {st(d.get('invites'))}, {d.get('seats')})",
       s == 200 and d.get("n") == 1 and st(d.get("invites")).get("ca-ac") == "ringing" and d.get("added") == []
       and d.get("seats") == {"used": 2, "limit": CL.ENDPOINTS_AUDIO, "left": CL.ENDPOINTS_AUDIO - 2, "topo": "mesh"})
    ev = sC.wait("call.invite", lambda x: x["call_id"] == g1, mC)
    ok("... Cora bekommt call.invite (laut)", ev and ev.get("silent") is False)

    # --- Aussenstehende: Rechte ------------------------------------------------------------------------------------------
    mD = sD.mark()
    s, d = add(B, g1, ["ca-ad"])
    ok(f"Bert (kein Admin) holt Dora (nicht im Chat): 403 rights mit Text ({s}, {d.get('error')})",
       s == 403 and d.get("rights") is True and "Admin" in (d.get("error") or "") and not member(gid, "ca-ad"))
    ok("... nichts geklingelt", sD.none("call.invite", mD, 0.6))

    # --- Admin holt Dora: wird Mitglied, klingelt, tritt bei ---------------------------------------------------------------
    s, d = add(A, g1, ["ca-ad"])
    ok(f"Anna holt Dora: 200, added, Mitglied, 'ringing' ({s}, {d.get('added')}, {st(d.get('invites')).get('ca-ad')}, n={d.get('n')})",
       s == 200 and d.get("added") == ["ca-ad"] and member(gid, "ca-ad") and st(d.get("invites")).get("ca-ad") == "ringing" and d.get("n") == 1)
    ev = sD.wait("call.invite", lambda x: x["call_id"] == g1, mD)
    ch = sD.wait("chat.changed", lambda x: x["chat_id"] == gid, mD)
    ok(f"... Dora: chat.changed + call.invite (Gruppenanruf, Chatname) ({bool(ch)}, {bool(ev)})",
       ch and ev and ev.get("kind") == "group" and ev.get("from", {}).get("name") == "Anna Beispiel")
    cd = get(D, g1)
    ok(f"... Dora sieht den Anruf: joinable, invite ringing ({cd.get('joinable')}, {cd.get('invite')})",
       cd.get("joinable") is True and cd.get("invite") == "ringing" and cd.get("state") in ("ringing", "active"))
    s, d = R_.join(D, D.dev, g1, [(A, A.dev), (B, B.dev)])
    ok(f"... Dora tritt bei (Angebote an Anna und Bert) ({s}, {len(d.get('call', {}).get('legs', []))} Endpunkte)",
       s == 200 and {x["who"] for x in d["call"]["legs"]} == {"ca-aa", "ca-ab", "ca-ad"})
    got = R_.got(A, A.dev, g1, D, D.dev)
    ok(f"... Annas Geraet bekommt Doras Angebot (geprueft) ({len(got)})", len(got) == 1 and got[0][1]["t"] == "offer")
    s, d = add(B, g1, ["ca-ad"])
    ok(f"Dora noch einmal hinzufuegen (schon im Anruf): 200, nichts geklingelt ({s}, n={d.get('n')})", s == 200 and d.get("n") == 0)

    # --- Handle eines Kontos hier, unbekannt, gesperrt ----------------------------------------------------------------
    with connect() as c, c:                               # legt die Handles an (Commit)
        hF = fed_chat.my_handle(c, "ca-af")
        hA = fed_chat.my_handle(c, "ca-aa")
    s, d = add(A, g1, [hF])
    ok(f"Anna holt Frieda per Handle '~x@host': Mitglied + klingelt ({s}, {d.get('added')})",
       s == 200 and d.get("added") == [hF] and member(gid, "ca-af") and st(d.get("invites")).get("ca-af") == "ringing")
    s, d = add(A, g1, ["ca-zz-niemand"])
    ok(f"Unbekannte Person: 404 mit Text ({s}, {d.get('error')})", s == 404 and "kein Chat" in (d.get("error") or "") and d.get("failed"))
    s, bd = req("POST", "/api/chat/blocks", E.k, {"target": hA})
    s2, d = add(A, g1, ["ca-ae"])
    ok(f"Emil hat Anna gesperrt: 404, nicht Mitglied ({s}, {bd}, {s2}, {d.get('error')})", s == 200 and s2 == 404 and not member(gid, "ca-ae"))
    s, d = add(A, g1, ["ca-ae", "ca-ac"])
    f0 = (d.get("failed") or [{}])[0]
    ok(f"Mischung (Emil gesperrt, Cora Mitglied): 200, Cora klingelt, failed nennt Emil ({s}, {d.get('failed')})",
       s == 200 and d.get("n") == 1 and f0.get("who") == "ca-ae" and f0.get("kind") == "unreachable" and f0.get("error"))
    ok("Texte ohne Anmeldenamen anderer", no_login_names(f0.get("error")))

    # --- Entfernte Person: nur der Gastgeber ---------------------------------------------------------------------------
    s, d = R_.join(Cc, Cc.dev, g1, [(A, A.dev), (B, B.dev), (D, D.dev)])
    s2, _ = req("POST", f"/api/calls/{g1}/moderate", A.k, {"device": A.dev["id"], "target": Cc.dev["id"], "action": "remove"})
    s3, d = add(B, g1, ["ca-ac"])
    ok(f"Gastgeberin entfernt Cora, Bert holt sie: 403 removed ({s}, {s2}, {s3}, {d.get('error')})",
       s == 200 and s2 == 200 and s3 == 403 and d.get("removed") is True)
    s, d = add(A, g1, ["ca-ac"])
    ok(f"... die Gastgeberin darf es (200, 'ringing') ({s}, {st(d.get('invites')).get('ca-ac')})", s == 200 and st(d.get("invites")).get("ca-ac") == "ringing")
    # Pruefung: Person eines anderen Servers, vom Gastgeber entfernt und nicht (mehr) im Chat - ein anderer Admin holt sie
    # per Handle zurueck: wie bei Konten hier 403 removed (vorher ging das an der L7-Regel vorbei)
    req("PATCH", f"/api/chats/{gid}/members/ca-ab", A.k, {"admin": True})
    fx = "@~" + "a" * 16 + "@fremd.example"
    with connect() as c, c:
        c.execute("INSERT INTO chat_call_invite (call_id, who, state, created) VALUES (?,?,'removed',?)", (g1, fx, time.time()))
    s, d = add(B, g1, [fx[1:]])
    with connect() as c, c:
        c.execute("DELETE FROM chat_call_invite WHERE call_id=? AND who=?", (g1, fx))
    ok(f"entfernte Person eines anderen Servers (nicht im Chat), Admin Bert holt sie per Handle: 403 removed ({s}, {d.get('error')})",
       s == 403 and d.get("removed") is True)
    req("PATCH", f"/api/chats/{gid}/members/ca-ab", A.k, {"admin": False})

    # --- Rate (selber Topf wie /invite) -----------------------------------------------------------------------------------
    s, d = req("POST", "/api/chats", Rr.k, {"kind": "free", "members": ["ca-ab"]})
    rid, g2 = d["chat"]["id"], secrets.token_hex(16)
    req("POST", "/api/calls", Rr.k, {"id": g2, "chat_id": rid, "media": "audio", "device": Rr.dev["id"], "caps": CAPS})
    codes = [add(Rr, g2, ["ca-ab"])[0] for _ in range(CL.CALL_RATE - 1)]
    codes.append(req("POST", f"/api/calls/{g2}/invite", Rr.k, {"uids": ["ca-ab"]})[0])
    s, d = add(Rr, g2, ["ca-ab"])
    ok(f"Rate: {CL.CALL_RATE} je Minute zusammen mit /invite, dann 429 mit Text ({codes}, {s})",
       codes == [200] * CL.CALL_RATE and s == 429 and "warten" in (d.get("error") or ""))
    req("POST", f"/api/calls/{g2}/end", Rr.k, {"device": Rr.dev["id"]})

    # --- 1:1 -> 409 dm ---------------------------------------------------------------------------------------------------
    dmc = A_.dm(F, Rr)
    c1, s, d = A_.call(F, Rr, dmc)
    s2, d2 = add(F, c1, ["ca-ac"])
    # 2.5 (L9): ein direkter 1:1 zieht nicht in eine Gruppe um (move_block direct, Hinweis) - Umzug ueber Knoten: anrufe_umzug
    ok(f"direkter 1:1-Anruf: 409 dm, move_block direct mit Text ({s}, {s2}, {d2.get('error')})",
       s == 200 and s2 == 409 and d2.get("dm") is True and d2.get("move_block") == "direct" and "Knoten" in (d2.get("error") or ""))
    req("POST", f"/api/calls/{c1}/end", F.k, {"device": F.dev["id"]})

    # --- Voll (Video: 4 Geraete, Annas Zweitgeraet zaehlt) -----------------------------------------------------------------
    req("POST", f"/api/calls/{g1}/end", A.k, {"device": A.dev["id"]})
    s, d = req("POST", "/api/chats", A.k, {"kind": "free", "members": ["ca-ab", "ca-ac"]})
    vid, g3 = d["chat"]["id"], secrets.token_hex(16)
    s, d = req("POST", "/api/calls", A.k, {"id": g3, "chat_id": vid, "media": "video", "device": A.dev["id"], "caps": CAPS})
    ends = [(A, A.dev)]
    for p, dv, role in ((B, B.dev, "member"), (Cc, Cc.dev, "member"), (A, A2, "companion")):
        s, _ = R_.join(p, dv, g3, list(ends), role=role)
        ends.append((p, dv))
    s, d = add(A, g3, ["ca-av"])
    ok(f"Voll (4 von 4 Geraeten, Zweitgeraet zaehlt): 409 full, seats, Text ({s}, {d.get('seats')}, {d.get('error')})",
       s == 409 and d.get("full") is True and d.get("seats", {}).get("used") == 4 and d.get("seats", {}).get("limit") == 4
       and "Gerät" in (d.get("error") or "") and not member(vid, "ca-av"))
    s, d = add(Cc, g3, ["ca-ab"])
    ok(f"... jemanden holen, der schon dabei ist: kein 409 ({s})", s == 200 and d.get("n") == 0)

    # --- Seiten-Chat: Bestaetigung "teilt die Seite" ----------------------------------------------------------------------
    s, d = req("POST", "/api/notes", G.k, {"title": "ca-Projektplan"})
    nid = d.get("id") or d.get("note", {}).get("id")
    req("PUT", f"/api/notes/{nid}/shares", G.k, {"type": "user", "target": "ca-ab", "role": "view"})
    s, d = req("POST", "/api/chats", G.k, {"kind": "page", "note_id": nid, "device": G.dev["id"]})
    pid = d["chat"]["id"]
    g4 = secrets.token_hex(16)
    s, d = req("POST", "/api/calls", G.k, {"id": g4, "chat_id": pid, "media": "audio", "device": G.dev["id"], "caps": CAPS})
    ok(f"Seiten-Chat: Anruf, Eigentuemer can_add + add_page = Titel ({s}, {d.get('call', {}).get('add_page')})",
       s == 200 and d["call"].get("can_add") is True and d["call"].get("add_page") == "ca-Projektplan")
    ok("... Bert (nur Lesen) can_add false", get(B, g4).get("can_add") is False)
    s, d = add(G, g4, ["ca-ad"])
    with connect() as c:
        sh = c.execute("SELECT role FROM note_share WHERE note_id=? AND target='ca-ad'", (nid,)).fetchone()
    ok(f"ohne Bestaetigung: 409 page_confirm mit Titel und Name, nichts geteilt ({s}, {d.get('error')})",
       s == 409 and d.get("page_confirm") is True and d.get("title") == "ca-Projektplan" and d.get("names") == ["Dora Neu"]
       and "Dora Neu bekommt Zugriff auf die Seite „ca-Projektplan“" in (d.get("error") or "") and sh is None and not member(pid, "ca-ad"))
    mD = sD.mark()
    s, d = add(G, g4, ["ca-ad"], confirm_page=True)
    with connect() as c:
        sh = c.execute("SELECT role FROM note_share WHERE note_id=? AND target='ca-ad'", (nid,)).fetchone()
    ok(f"mit confirm_page: Seite geteilt (Lesen), Mitglied, klingelt ({s}, {sh}, {st(d.get('invites')).get('ca-ad')})",
       s == 200 and sh and sh[0] == "view" and member(pid, "ca-ad") and st(d.get("invites")).get("ca-ad") == "ringing")
    ok("... Dora bekommt call.invite", sD.wait("call.invite", lambda x: x["call_id"] == g4, mD))
    s, d = add(B, g4, ["ca-ae"], confirm_page=True)
    ok(f"Bert (nicht Eigentuemer) holt jemanden in den Seiten-Chat: 403 mit Text ({s}, {d.get('error')})",
       s == 403 and "Seite gehört" in (d.get("error") or ""))
    s, d = add(G, g4, ["ca-ab"])
    ok(f"Seiten-Chat, nur Mitglied klingeln: keine Bestaetigung noetig ({s})", s == 200)

    # --- beendet ---------------------------------------------------------------------------------------------------------
    req("POST", f"/api/calls/{g4}/end", G.k, {"device": G.dev["id"]})
    req("POST", f"/api/calls/{g3}/end", A.k, {"device": A.dev["id"]})
    with connect() as c, c:
        c.execute("UPDATE chat_call SET state='ended', ended=? WHERE id=?", (time.time(), g4))
    s, d = add(G, g4, ["ca-ab"])
    ok(f"beendeter Anruf: 409 ended ({s})", s == 409 and d.get("ended") is True)
    s, d = add(A, g4, ["ca-ab"])
    ok(f"fremder Anruf (nicht Mitglied): 404 ({s})", s == 404)
    s, d = add(A, g3, [])
    ok(f"ohne uids: 400 ({s})", s == 400)
    s, d = add(A, g3, "ca-ab")
    ok(f"uids als Text statt Liste: 400 ({s})", s == 400)


if __name__ == "__main__":
    main()
