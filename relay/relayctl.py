#!/usr/bin/env python3
"""Betrieb des Push-Relais (2262): Server auflisten, freischalten, sperren, Stufen und Grenzen setzen, Statistik.

  relayctl.py [--db /data/relay.db] list [--state pending]
  relayctl.py show <server_id>
  relayctl.py approve <server_id>            pending -> active
  relayctl.py block <server_id> [--note TEXT]
  relayctl.py unblock <server_id>            -> active, Stufe neu (7 Tage bis aktiv)
  relayctl.py stage <server_id> neu|aktiv|eigen
  relayctl.py limits <server_id> '{"alert": {"server": 3000}}'   (setzt Stufe eigen)
  relayctl.py stats [<server_id>] [--days 7]

Im Container: docker exec saga-relay python relayctl.py list. Wirkt sofort (der Dienst liest den Server je Anfrage)."""
import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)                                  # Image: /app (sagarelay/, saga/fedsig.py)
if os.path.exists(os.path.join(_HERE, "..", "saga", "fedsig.py")):
    sys.path.insert(1, os.path.join(_HERE, ".."))          # Repo: relay/ neben saga/

from sagarelay import config, store as store_mod  # noqa: E402


def _ts(t):
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(t)) if t else "-"


def _get(st, sid):
    s = st.get(sid)
    if not s:
        print(f"unbekannt: {sid}", file=sys.stderr)
        sys.exit(2)
    return s


def main(argv=None):
    ap = argparse.ArgumentParser(prog="relayctl", description="Saga-Push-Relais verwalten")
    ap.add_argument("--db", default=os.environ.get("RELAY_DB", "/data/relay.db"))
    ap.add_argument("--json", action="store_true", help="Ausgabe als JSON")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list")
    p.add_argument("--state", choices=("active", "pending", "blocked"))
    for n in ("show", "approve", "unblock"):
        sub.add_parser(n).add_argument("server_id")
    p = sub.add_parser("block")
    p.add_argument("server_id")
    p.add_argument("--note", default="")
    p = sub.add_parser("stage")
    p.add_argument("server_id")
    p.add_argument("stage", choices=config.STAGES)
    p = sub.add_parser("limits")
    p.add_argument("server_id")
    p.add_argument("values")
    p = sub.add_parser("stats")
    p.add_argument("server_id", nargs="?")
    p.add_argument("--days", type=int, default=7)
    a = ap.parse_args(argv)
    if not os.path.exists(a.db):
        print(f"Datenbank fehlt: {a.db}", file=sys.stderr)
        return 2
    st = store_mod.Store(a.db)
    now = int(time.time())

    if a.cmd == "list":
        rows = [s for s in st.all() if not a.state or s["state"] == a.state]
        if a.json:
            print(json.dumps(rows, indent=1))
            return 0
        print(f"{'server_id':20} {'state':8} {'stage':6} {'nachweis':8} {'zuletzt':16} host")
        for s in rows:
            print(f"{s['server_id']:20} {s['state']:8} {s['stage']:6} {'ja' if s['verified'] else 'nein':8} "
                  f"{_ts(s['last_seen']):16} {s['host']}")
        return 0
    if a.cmd == "show":
        s = _get(st, a.server_id)
        from saga import fedsig
        s["fingerprint"] = fedsig.fingerprint(fedsig.load_public(s["pub"]))
        print(json.dumps(s, indent=1, ensure_ascii=False))
        return 0
    if a.cmd == "approve":
        s = _get(st, a.server_id)
        if s["state"] == "blocked":
            print("gesperrt - erst unblock", file=sys.stderr)
            return 1
        st.update(s["server_id"], state="active", clean_since=now)
    elif a.cmd == "block":
        s = _get(st, a.server_id)
        st.update(s["server_id"], state="blocked", note=a.note[:300])
    elif a.cmd == "unblock":
        s = _get(st, a.server_id)
        st.update(s["server_id"], state="active", stage="neu" if s["stage"] != "eigen" else "eigen", clean_since=now)
    elif a.cmd == "stage":
        s = _get(st, a.server_id)
        st.update(s["server_id"], stage=a.stage, clean_since=now if a.stage == "neu" else s["clean_since"])
    elif a.cmd == "limits":
        s = _get(st, a.server_id)
        try:
            v = json.loads(a.values)
            config.merge_tier(config.DEFAULT_TIERS["aktiv"], v)
        except (ValueError, config.ConfigError) as ex:
            print(f"ungueltig: {ex}", file=sys.stderr)
            return 1
        st.update(s["server_id"], limits=v, stage="eigen")
    elif a.cmd == "stats":
        rows = st.stats(a.server_id, a.days)
        if a.json:
            print(json.dumps([dict(zip(("day", "server_id", "push_type", "status", "n"), r)) for r in rows], indent=1))
            return 0
        for r in rows:
            print(f"{r[0]} {r[1]:20} {r[2]:12} {r[3]:3} {r[4]}")
        return 0
    s = st.get(a.server_id)
    print(f"{s['server_id']}: state={s['state']} stage={s['stage']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
