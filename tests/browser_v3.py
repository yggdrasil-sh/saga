"""Chat-Stufe 3 im Browser (2.5): das Web (Stufe 2, Kompat-Sicht v: 2) telefoniert und schreibt in lokalen Chats der Stufe 3.
Schaltet chat_v3 ein, entfernt bestehende Chats der beiden Browser-Konten (damit neue in Stufe 3 entstehen) und laesst
tests/browser_calls.js (1:1-Anruf), tests/browser_klingeln.js (Klingeln bei Anrufen kurz hintereinander), tests/browser_calls3.js (Raum, weiteres Geraet), tests/browser_gelesen.js und
tests/browser_x3.js (Reaktionen, Bearbeiten), tests/browser_voice.js (Sprachnachrichten) laufen, vorher tests/browser_trust3.js (Vertrauens-Uebernahme bei der Umstellung) (BV3_JS=a.js,b.js waehlt aus).
Danach: die Chats der beiden sind Stufe 3, Anrufe liefen darin, Nachrichten im v2-Format (sender_handle), und der Server
hat keinem der beiden den Anmeldenamen des anderen in einem Anruf-Eintrag gezeigt.
Umgebung wie die Browser-Tests (SAGA_BASE, SAGA_USER/PASS, SAGA_USER2/PASS2, PLAYWRIGHT, PLAYWRIGHT_BROWSERS_PATH) plus
Datenbank ueber db.py. Ohne Playwright/Konten: uebersprungen."""
import json, os, subprocess, sys, time

import auth
from db import connect

HERE = os.path.dirname(os.path.abspath(__file__))
E = os.environ
res = []


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, ("  -- " + str(info)[:500]) if (info and not c) else "", flush=True)


def wipe(a, b):
    with connect() as c, c:
        c.execute("DELETE FROM chat WHERE id IN (SELECT chat_id FROM chat_member WHERE uid IN (?,?))", (a, b))
        # Geraete frueherer Reihen abmelden (jede Reihe startet mit frischen Browsern): sonst ist in all.sh das Geraete-Limit
        # je Konto erreicht (Wiederherstellung 409), und Geraete ohne chat3 halten die Umstellung auf Stufe 3 bis zum Stichtag auf
        c.execute("UPDATE chat_device SET revoked_at=? WHERE uid IN (?,?) AND revoked_at IS NULL", (time.time(), a, b))


def trust3(a, b):
    """Vertrauens-Uebernahme im Browser: Phase 1 mit chat_v3 aus (Chat v1), umstellen (saga.chat.convert_v3), Phase 2."""
    import shutil, tempfile
    from saga import chat as CH, fed_chat     # fed_chat setzt chat.FED - wie im Server
    assert fed_chat
    prof = tempfile.mkdtemp(prefix="saga-trust3-")
    try:
        wipe(a, b)
        auth.save_setting("chat_v3", False)
        time.sleep(5.6)
        for ph in ("1", "2"):
            if ph == "2":
                auth.save_setting("chat_v3", True)
                time.sleep(5.6)
                n = CH.convert_v3(limit=100000)
                ok(f"browser_trust3.js: umgestellt ({n})", n >= 1, n)
            r = subprocess.run(["node", os.path.join(HERE, "browser_trust3.js")], capture_output=True, text=True, timeout=900,
                               env=dict(E, PHASE=ph, PROFILES=prof))
            last = (r.stdout.strip().splitlines() or ["?"])[-1]
            fails = [x for x in r.stdout.splitlines() if x.startswith("FAIL")]
            ok(f"browser_trust3.js Phase {ph}: {last}", r.returncode == 0 and not fails, fails[:5] or r.stderr[-500:])
            if r.returncode != 0 or fails:
                print(r.stdout[-4000:], flush=True)
                break
    finally:
        shutil.rmtree(prof, ignore_errors=True)


def main():
    a, b = E.get("SAGA_USER"), E.get("SAGA_USER2")
    if not (E.get("SAGA_BASE") and a and b and E.get("SAGA_PASS") and E.get("SAGA_PASS2")):
        print("BROWSER-V3 uebersprungen (SAGA_BASE/SAGA_USER/... fehlen) - ALLES GRUEN")
        return 0
    prev = auth.settings(fresh=True).get("chat_v3")
    auth.save_setting("chat_v3", True)
    time.sleep(5.6)
    try:
        all_js = ("browser_calls.js", "browser_klingeln.js", "browser_calls3.js", "browser_gelesen.js", "browser_x3.js", "browser_voice.js")
        if not E.get("BV3_JS") or "browser_trust3.js" in E["BV3_JS"].split(","):
            trust3(a, b)
            auth.save_setting("chat_v3", True)
            time.sleep(5.6)
        for js in [x for x in all_js if not E.get("BV3_JS") or x in E["BV3_JS"].split(",")]:
            wipe(a, b)
            r = subprocess.run(["node", os.path.join(HERE, js)], capture_output=True, text=True, timeout=900, env=dict(E))
            last = (r.stdout.strip().splitlines() or ["?"])[-1]
            fails = [x for x in r.stdout.splitlines() if x.startswith("FAIL")]
            ok(f"{js} in Stufe 3: {last}", r.returncode == 0 and not fails, fails[:5] or r.stderr[-500:])
            if r.returncode != 0 or fails:
                print(r.stdout[-4000:], flush=True)
            with connect() as c:
                chats = c.execute("SELECT DISTINCT ch.id, ch.v, ch.kind FROM chat ch JOIN chat_member m ON m.chat_id=ch.id WHERE m.uid IN (?,?)",
                                  (a, b)).fetchall()
                calls = c.execute("SELECT COUNT(*) FROM chat_call k JOIN chat ch ON ch.id=k.chat_id WHERE ch.v=3 AND (k.caller IN (?,?) OR k.kind='group')",
                                  (a, b)).fetchone()[0]
                v1 = c.execute("SELECT COUNT(*) FROM chat_msg m JOIN chat_member x ON x.chat_id=m.chat_id WHERE x.uid=? AND m.kind='msg' "
                               "AND m.sender_handle IS NULL", (a,)).fetchone()[0]
                logs = [r[0] for r in c.execute("SELECT m.body_ct FROM chat_msg m JOIN chat ch ON ch.id=m.chat_id WHERE ch.v=3 AND m.kind='call' "
                                                "AND m.chat_id IN (SELECT chat_id FROM chat_member WHERE uid=?)", (a,)).fetchall()]
            ok(f"{js}: alle Chats der beiden in Stufe 3 ({len(chats)})", chats and all(x[1] == 3 for x in chats), chats)
            if js == "browser_x3.js":
                with connect() as c:
                    rx = c.execute("SELECT COUNT(*) FROM chat_reaction r JOIN chat_msg m ON m.seq=r.msg_seq JOIN chat ch ON ch.id=m.chat_id "
                                   "WHERE ch.v=3 AND m.chat_id IN (SELECT chat_id FROM chat_member WHERE uid=?)", (a,)).fetchone()[0]
                    ed = c.execute("SELECT COUNT(*) FROM chat_msg m WHERE m.kind='edit' AND m.chat_id IN (SELECT chat_id FROM chat_member WHERE uid=?)",
                                   (a,)).fetchone()[0]
                ok(f"{js}: Reaktionen ({rx}) und je bearbeiteter Nachricht eine Bearbeitung ({ed}) gespeichert", rx >= 2 and ed == 2, (rx, ed))
            elif js not in ("browser_gelesen.js", "browser_voice.js"):
                ok(f"{js}: Anrufe liefen in Stufe-3-Chats ({calls})", calls > 0)
                ok(f"{js}: Anruf-Eintraege ohne Anmeldenamen ({len(logs)})", logs and not any(a in x or b in x for x in logs)
                   and all(json.loads(x)["from"].startswith("~") for x in logs), logs[:2])
            ok(f"{js}: Nachrichten nur im v2-Format", v1 == 0, v1)
    finally:
        auth.save_setting("chat_v3", bool(prev))
        wipe(a, b)
    n = sum(res)
    print(f"\nBROWSER-V3 {n}/{len(res)} - " + ("ALLES GRUEN" if n == len(res) else "FEHLER"))
    return 0 if n == len(res) else 1


if __name__ == "__main__":
    sys.exit(main())
