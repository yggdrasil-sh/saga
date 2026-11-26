#!/bin/sh
# Rahmen fuer tests/browser_kollegin_say.js (2268): Wegwerf-Konto ks-w + Admin ks-wadm, KI an (Fake-Adresse, kein Aufruf),
# Cookies, Lauf, Aufraeumen. Aufruf auf saga-test im Worktree: sh tests/kollegin_say_browser.sh [Bilder-Ordner]
set -e
SHOTS="${1:-}"
export SHOTS
eval "$(PYTHONPATH=tests/pgshim:.:tests python - <<'PY'
import json, time, auth, ki
from db import connect
prev = auth.settings(fresh=True)
open("/tmp/ks-w-prev.json", "w").write(json.dumps({"ai": prev["ai"], "p": prev.get("ai_persona") or {}}))
for u, adm in (("ks-w", False), ("ks-wadm", True)):
    auth.get_user(u) or auth.create_user(u, ki.PW, u, is_admin=adm)
auth.save_setting("ai", dict(prev["ai"], enabled=True, provider="openai", url="http://127.0.0.1:9/v1", model="fake", groups=[], pinned=["127.0.0.1"]))
auth.save_setting("ai_persona", {"name": "Marin", "tone": "", "form": "sie", "lively": "warm", "salutation": "du"})
a = ki.Admin("ks-wadm")
print(f"export CK='{ki.ck('ks-w')}' CKA='{a.cookie}'")
PY
)"
sleep 6
curl -s -o /dev/null -X POST -H 'X-Saga: 1' -H 'Content-Type: application/json' -b "saga=$CK" -d '{"answer":"no"}' "$HK_TEST_BASE/api/first-steps" || true
rc=0
node tests/browser_kollegin_say.js || rc=$?
PYTHONPATH=tests/pgshim:. python - <<'PY'
import json, auth
from saga.userdata import delete_user_data
from db import connect
p = json.load(open("/tmp/ks-w-prev.json"))
auth.save_setting("ai", p["ai"]); auth.save_setting("ai_persona", p["p"])
for u in ("ks-w", "ks-wadm"):
    delete_user_data(u)
with connect() as c, c:
    c.execute("DELETE FROM user_kv WHERE uid LIKE 'ks-w%'"); c.execute("DELETE FROM users WHERE username LIKE 'ks-w%'")
PY
exit $rc
