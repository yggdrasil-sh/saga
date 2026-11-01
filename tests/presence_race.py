"""POST /api/presence (saga/live.py api_presence) ohne Server: ein gleichzeitiges {leave} desselben Kontos (zweiter Tab,
Seitenwechsel) waehrend des await auf avatars.my_url entfernte _presence[nid][uid] - danach KeyError -> 500.
Nachgestellt im Prozess: _api_guard/_body/_here_db/avatars.my_url ersetzt, leave laeuft genau in diesem await."""
import asyncio, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from saga import live  # noqa: E402

res = []


def ok(n, c):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, flush=True)


NID, UID = 4711, "pr-a"
BODIES = []
live._api_guard = lambda req: UID


async def _body(req):
    return BODIES.pop(0)
live._body = _body
live._here_db = lambda uid, nid, entries: ({"rev": 1, "updated": 0, "by": None}, [])   # nur ich selbst
live._S["q"] = None


class _Req:
    session = {"name": "Anna"}


def my_url(uid):          # waehrenddessen verlaesst ein anderer Tab desselben Kontos die Seite
    BODIES.append({"note_id": NID, "leave": True})
    asyncio.run_coroutine_threadsafe(live.api_presence(_Req()), LOOP).result(5)
    return None
live.avatars.my_url = my_url


async def main():
    global LOOP
    LOOP = asyncio.get_running_loop()
    BODIES.append({"note_id": NID, "editing": True, "block": "b1"})
    try:
        r = await asyncio.wait_for(live.api_presence(_Req()), 10)
    except KeyError as ex:
        ok(f"Anwesenheit mit gleichzeitigem leave: kein KeyError ({ex!r})", False)
        return
    d = json.loads(r.body)
    me = [u for u in d.get("users", []) if u.get("me")]
    ok("Anwesenheit mit gleichzeitigem leave: 200 statt KeyError", r.status_code == 200)
    ok("... eigener Eintrag mit editing/block der Anfrage", me and me[0]["editing"] is True and me[0]["block"] == "b1")


asyncio.run(main())
print(f"{sum(res)}/{len(res)}", "ALLES GRUEN" if all(res) else "FEHLER")
sys.exit(0 if all(res) else 1)
