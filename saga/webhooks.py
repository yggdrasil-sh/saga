"""Webhooks (2.5, Paket 1 E6; Konzept 2.8): Saga meldet Aenderungen per HTTP-POST an eine Adresse der Person.

- Ereignisse (EVENTS): page.created/updated/trashed/restored, row.created/updated/deleted/assigned, comment.created,
  task.created/completed und ping (Test). Erzeugt in DERSELBEN Transaktion ueber saga.bus-Zuhoerer -> Ausgangskorb
  webhook_delivery (wie fed_outbox). Autosave: eine noch nicht zugestellte Meldung derselben (Hook, Seite, Art) wird
  zusammengefasst (next_try = jetzt + DEBOUNCE, geaenderte Eigenschaften vereinigt).
- Nutzlast duenn: {id, type, created, data: {note_id, db_id, title, changed, actor: {handle, name}}} - kein Inhalt; Personen
  nur als Handle. Rechte werden bei der Zustellung NEU geprueft (Konto aktiv, API frei, Token gueltig, Leserecht auf die
  Seite, roots) - sonst wird nichts gesendet (status "dropped").
- Signatur: Saga-Signature: t=<unix>,v1=<hex HMAC-SHA256(geheimnis, t + "." + body)>, dazu Saga-Event, Saga-Delivery.
  Geheimnis 32 Byte, verschluesselt gespeichert (auth._enc), nur beim Anlegen/Erneuern einmal sichtbar.
- Zustellung: eigener Hintergrund-Thread, hoechstens PARALLEL gleichzeitig, Zeitlimit 5 s, Antwort hoechstens 1 KB gelesen,
  KEINE Weiterleitungen (3xx = Fehler), 2xx = zugestellt. SSRF: netguard.guarded_client bei JEDER Zustellung (Aufloesen
  und Pruefen beim Verbindungsaufbau, gegen DNS-Rebinding): oeffentliche Adressen; interne nur mit Admin-Freigabe
  (allow_private_hosts bzw. allowed_hosts - ein dort mit ":port" eingetragener Host darf auch diesen Port nutzen).
  Nur https (Port 443), http (Port 80) nur, wenn der Admin es erlaubt. SAGA_RESTORE_TEST=1: nichts geht raus.
- Wiederholungen: 1 min, 5 min, 30 min, 2 h, 6 h, 12 h, 24 h, dann "failed". 20 Fehlschlaege in Folge: Hook aus +
  Mitteilung an das Konto. Protokoll: die letzten LOG_KEEP Zustellungen je Hook.

Web (Sitzung + X-Saga): GET/POST /api/account/webhooks, PATCH/DELETE /api/account/webhooks/{id},
POST /api/account/webhooks/{id}/test|rotate. API (Bereich "webhooks"): GET/POST /api/v1/webhooks,
DELETE /api/v1/webhooks/{id}, GET /api/v1/webhooks/{id}/deliveries, POST /api/v1/webhooks/{id}/test."""
import hashlib
import hmac
import json
import re
import secrets
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import auth
from db import clean_line, connect as _db
from saga import apitok, bus, netguard, notify
from saga.core import E, RESTORE_TEST, _api_guard, _bad, _body, _json_load, local_ref, t, user_info
from saga.sharing import Access, is_remote, names
from version import VERSION

router = APIRouter()

EVENTS = ("page.created", "page.updated", "page.trashed", "page.restored", "row.created", "row.updated", "row.deleted",
          "row.assigned", "comment.created", "task.created", "task.completed")
EVENT_SCOPE = {"page": "read:pages", "row": "read:db", "comment": "read:comments", "task": "read:tasks"}
HOOKS_MAX = 10
URL_MAX = 500
DEBOUNCE = float(E.get("SAGA_WEBHOOK_DEBOUNCE", "10"))          # Sekunden (Tests kuerzer)
RETRY = tuple(x * float(E.get("SAGA_WEBHOOK_RETRY_SCALE", "1")) for x in (60, 300, 1800, 7200, 21600, 43200, 86400))
FAIL_OFF = 20
TIMEOUT = 5.0
READ_MAX = 1024
PARALLEL = 4
LOG_KEEP = 50
TREE_MAX = 50                                                   # Teilbaum in den Papierkorb: hoechstens so viele Meldungen


def conf():
    return apitok.conf()


# --- Adresse pruefen ---------------------------------------------------------------------------------------------
def _allowed_hosts():
    return {h.strip().lower() for h in re.split(r"[,\s]+", auth.settings().get("allowed_hosts") or "") if h.strip()}


def _ip_ok(ip, host, port):
    """Oeffentliche Adressen immer; interne nur mit Freigabe im Admin (wie Foederation/Kalender)."""
    if netguard.is_public(ip):
        return True
    if auth.settings().get("allow_private_hosts"):
        return True
    hs = _allowed_hosts()
    return f"{host}:{port}".lower() in hs or str(host).lower() in hs


def check_url(url, cf=None):
    """-> (normalisierte URL, None) oder (None, Fehlertext). Form, Schema, Port, keine Zugangsdaten."""
    cf = cf or conf()
    url = clean_line(str(url or "")).strip()
    if not url or len(url) > URL_MAX:
        return None, t("Bitte eine Adresse angeben (höchstens {n} Zeichen).", n=URL_MAX)
    try:
        u = urlsplit(url)
        port = u.port
    except ValueError:
        return None, t("Die Adresse ist ungültig.")
    if u.scheme not in ("https", "http") or not u.hostname:
        return None, t("Die Adresse muss mit https:// beginnen.")
    if u.scheme == "http" and not cf.get("webhook_http"):
        return None, t("Nur https:// ist erlaubt (http:// hat der Admin nicht freigegeben).")
    if u.username or u.password:
        return None, t("Zugangsdaten in der Adresse sind nicht erlaubt.")
    if u.fragment:
        return None, t("Die Adresse ist ungültig.")
    std = 443 if u.scheme == "https" else 80
    if port not in (None, std) and f"{u.hostname}:{port}".lower() not in _allowed_hosts():
        return None, t("Nur der Standard-Port ist erlaubt (andere Ports nur mit Freigabe des Admins).")
    return url, None


def check_target(url):
    """Beim Anlegen zusaetzlich: Name aufloesen, alle Adressen pruefen (bei jeder Zustellung wird erneut geprueft)."""
    u = urlsplit(url)
    port = u.port or (443 if u.scheme == "https" else 80)
    if RESTORE_TEST:
        return t("Testwiederherstellung: Webhooks sind abgeschaltet.")
    try:
        infos = netguard.lookup(u.hostname, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return t("Der Name lässt sich nicht auflösen.")
    for i in infos:
        if not _ip_ok(i[4][0], u.hostname, port):
            return t("Interne Adressen sind nur mit Freigabe des Admins erlaubt.")
    return None


# --- Verwaltung --------------------------------------------------------------------------------------------------
def _events_in(raw, tok=None):
    if raw in (None, "all", ["*"]):
        raw = list(EVENTS)
    if not isinstance(raw, list) or not raw:
        raise ValueError(t("Mindestens ein Ereignis wählen."))
    out = []
    for e in raw:
        if e not in EVENTS:
            raise ValueError(t("Unbekanntes Ereignis: {e}", e=str(e)[:40]))
        if tok is not None and not tok.has(EVENT_SCOPE[e.split(".")[0]]):
            raise ValueError(t("Dem Token fehlt der Bereich „{s}“.", s=EVENT_SCOPE[e.split(".")[0]]))
        out.append(e)
    return [e for e in EVENTS if e in out]


def _hook_out(r, log=None):
    d = {"id": r[0], "name": r[1], "url": r[2], "events": _json_load(r[3], []), "active": bool(r[4]), "fail_streak": r[5],
         "created": r[6], "last_ok": r[7], "last_error": r[8] or "", "token": bool(r[9]), "roots": apitok.roots_load(r[10])}
    if log is not None:
        d["deliveries"] = log
    return d


HOOK_COLS = "id, name, url, events, active, fail_streak, created, last_ok, last_error, token_id, roots"


def _log(c, hid, n=LOG_KEEP):
    return [{"id": r[0], "event": r[1], "type": r[2], "note_id": r[3], "created": r[4], "status": r[5], "attempts": r[6],
             "code": r[7], "error": r[8] or "", "done": r[9], "next_try": r[10] if r[5] == "pending" else None}
            for r in c.execute("SELECT id, event_id, kind, note_id, created, status, attempts, last_code, last_error, done, next_try "
                               "FROM webhook_delivery WHERE hook_id=? ORDER BY id DESC LIMIT ?", (hid, n)).fetchall()]


def list_hooks(uid, with_log=False):
    with _db() as c:
        rows = c.execute(f"SELECT {HOOK_COLS} FROM webhook WHERE uid=? ORDER BY id", (uid,)).fetchall()
        return [_hook_out(r, _log(c, r[0], 10) if with_log else None) for r in rows]


def create_hook(uid, b, tok=None):
    """-> (antwort, None) oder (None, (status, fehler)). Geheimnis nur in dieser Antwort."""
    cf = conf()
    if not cf.get("webhooks") or not apitok.allowed_for(user_info(uid), cf):
        return None, (403, t("Webhooks sind auf diesem Server abgeschaltet."))
    url, err = check_url(b.get("url"), cf)
    if err:
        return None, (400, err)
    err = check_target(url)
    if err:
        return None, (400, err)
    try:
        events = _events_in(b.get("events"), tok)
    except ValueError as ex:
        return None, (400, str(ex))
    name = re.sub(r"\s+", " ", clean_line(str(b.get("name") or ""))).strip()[:60] or urlsplit(url).hostname
    secret = "whsec_" + secrets.token_urlsafe(32)
    with _db() as c, c:
        if c.execute("SELECT COUNT(*) FROM webhook WHERE uid=?", (uid,)).fetchone()[0] >= HOOKS_MAX:
            return None, (409, t("Höchstens {n} Webhooks je Konto.", n=HOOKS_MAX))
        hid = c.execute("INSERT INTO webhook (uid, url, secret_enc, events, roots, token_id, created, name) VALUES (?,?,?,?,?,?,?,?)",
                        (uid, url, auth._enc(secret), json.dumps(events), json.dumps(tok.roots) if tok and tok.roots else None,
                         tok.id if tok else None, time.time(), name)).lastrowid
    _any.update(t=time.time(), v=True)
    print(f"webhooks: Hook {hid} angelegt ({', '.join(events)})", flush=True)
    return {"id": hid, "name": name, "url": url, "events": events, "secret": secret}, None


def delete_hook(uid, hid):
    with _db() as c, c:
        return c.execute("DELETE FROM webhook WHERE id=? AND uid=?", (hid, uid)).rowcount


def update_hook(uid, hid, b):
    sets, args = [], []
    try:
        if "events" in b:
            sets.append("events=?"); args.append(json.dumps(_events_in(b["events"])))
        if "name" in b:
            sets.append("name=?"); args.append(re.sub(r"\s+", " ", clean_line(str(b.get("name") or ""))).strip()[:60])
    except ValueError as ex:
        return 400, {"error": str(ex)}
    with _db() as c, c:
        r = c.execute("SELECT token_id FROM webhook WHERE id=? AND uid=?", (hid, uid)).fetchone()
        if not r:
            return 404, {"error": "not found"}
        if "active" in b:
            if b["active"] and r[0] is not None and not _token_ok(c, r[0]):
                return 400, {"error": t("Der zugehörige API-Token ist widerrufen oder abgelaufen.")}
            sets.append("active=?"); args.append(1 if b["active"] else 0)
            if b["active"]:
                sets.append("fail_streak=0")
        if not sets:
            return 400, {"error": t("nichts zu ändern")}
        c.execute(f"UPDATE webhook SET {', '.join(sets)} WHERE id=? AND uid=?", (*args, hid, uid))
    _any["t"] = 0.0
    return 200, {"ok": True}


def rotate(uid, hid):
    secret = "whsec_" + secrets.token_urlsafe(32)
    with _db() as c, c:
        n = c.execute("UPDATE webhook SET secret_enc=? WHERE id=? AND uid=?", (auth._enc(secret), hid, uid)).rowcount
    return secret if n else None


def ping(uid, hid):
    with _db() as c, c:
        if not c.execute("SELECT 1 FROM webhook WHERE id=? AND uid=?", (hid, uid)).fetchone():
            return None
        eid = _enqueue_one(c, hid, "ping", None, None, uid, [], now=time.time(), debounce=False)
    return eid


def _token_ok(c, tid):
    r = c.execute("SELECT revoked, expires FROM api_token WHERE id=?", (tid,)).fetchone()
    return bool(r) and r[0] is None and (r[1] is None or r[1] > time.time())


# --- Ereignisse -> Ausgangskorb -------------------------------------------------------------------------------------
_wake = threading.Event()


_any = {"t": 0.0, "v": True}


def _any_hooks(c):
    """Gibt es ueberhaupt aktive Hooks? (Speichern ist heiss: hoechstens alle 10 s nachsehen; Anlegen setzt sofort.)"""
    now = time.time()
    if now - _any["t"] > 10:
        _any.update(t=now, v=bool(c.execute("SELECT 1 FROM webhook WHERE active=1 LIMIT 1").fetchone()))
    return _any["v"]


def _hooks_for(c, kind):
    """Aktive Hooks, die diese Art abonniert haben: [(id, uid, roots)]."""
    if not conf().get("webhooks") or not _any_hooks(c):
        return []
    return [(r[0], r[1], apitok.roots_load(r[2])) for r in c.execute(
        "SELECT id, uid, roots FROM webhook WHERE active=1 AND events LIKE ?", (f'%"{kind}"%',)).fetchall()]


def _enqueue_one(c, hid, kind, note_id, db_id, actor, changed, now, debounce=True):
    if debounce and note_id is not None:
        r = c.execute("SELECT id, changed FROM webhook_delivery WHERE hook_id=? AND note_id=? AND kind=? AND status='pending' "
                      "AND attempts=0 AND locked_until IS NULL ORDER BY id DESC LIMIT 1 FOR UPDATE", (hid, note_id, kind)).fetchone()
        if r:                                       # Autosave: zusammenfassen und Zustellung nach hinten schieben
            ch = list(dict.fromkeys(_json_load(r[1], []) + list(changed or [])))[:100]
            c.execute("UPDATE webhook_delivery SET changed=?, actor=?, next_try=? WHERE id=?", (json.dumps(ch), actor, now + DEBOUNCE, r[0]))
            return None
    eid = "evt_" + uuid.uuid4().hex
    c.execute("INSERT INTO webhook_delivery (hook_id, event_id, kind, note_id, db_id, actor, changed, created, next_try) VALUES (?,?,?,?,?,?,?,?,?)",
              (hid, eid, kind, note_id, db_id, actor, json.dumps(list(changed or [])[:100]), now, now + (DEBOUNCE if debounce else 0)))
    c.after_commit(_wake.set)
    return eid


def _db_of(c, owner, nid):
    r = c.execute("SELECT d.id FROM note n JOIN note d ON d.id=n.parent_id AND d.uid=n.uid WHERE n.id=? AND n.uid=? AND d.kind='database'",
                  (nid, owner)).fetchone()
    return r[0] if r else None


def _visible(c, uid, nid, roots):
    acc = Access(c, uid)
    if not acc.role(nid):
        return False
    if not roots:
        return True
    owner = acc.owner(nid)
    tok = apitok.Tok(0, uid, "", "", (), roots, None)
    if apitok.in_roots(c, tok, owner, nid):
        return True
    # schon im Papierkorb (Kette endet an der Seite selbst): eigene Seite, die Wurzel ist oder unter einer liegt
    r = c.execute("SELECT parent_id, deleted_at FROM note WHERE id=? AND uid=?", (nid, owner)).fetchone()
    return bool(r and r[1] is not None and owner == uid and (int(nid) in tok.roots or (r[0] and apitok.in_roots(c, tok, owner, r[0]))))


def _emit(c, kind, owner, nid, actor, changed=(), debounce=False, db_id=None):
    hooks = _hooks_for(c, kind)
    if not hooks:
        return
    now = time.time()
    for hid, uid, roots in hooks:
        if (uid == owner and not roots) or _visible(c, uid, nid, roots):
            _enqueue_one(c, hid, kind, nid, db_id, actor, changed, now, debounce)


def _on_content(c, owner, note_id, by=None, **_):
    """Inhalt gespeichert (pages._note_update). Titel/Eigenschaften einer Zeile: row.changed, Titel einer Seite: notes.tree."""
    if is_remote(owner):
        return
    dbid = _db_of(c, owner, note_id)
    if dbid is not None:
        _emit(c, "row.updated", owner, note_id, by, ["body"], debounce=True, db_id=dbid)
    else:
        _emit(c, "page.updated", owner, note_id, by, ["body"], debounce=True)


def _on_row(c, owner, note_id, by=None, changed=(), **_):
    """Eigenschaften/Titel geaendert: Zeile -> row.updated, Seite (nur Titel) -> page.updated."""
    dbid = _db_of(c, owner, note_id)
    _emit(c, "row.updated" if dbid is not None else "page.updated", owner, note_id, by, list(changed), debounce=True, db_id=dbid)


def _on_tree(c, owner, ids, op, **_):
    if op not in ("created", "trashed", "restored") or not ids or is_remote(owner):
        return
    for nid in list(ids)[:TREE_MAX]:
        r = c.execute("SELECT updated_by, created_by FROM note WHERE id=? AND uid=?", (nid, owner)).fetchone()
        actor = (r[1] if op == "created" else r[0]) if r else None
        dbid = _db_of(c, owner, nid)
        if dbid is not None:
            kind = {"created": "row.created", "trashed": "row.deleted", "restored": "row.created"}[op]
        else:
            kind = "page." + op
        _emit(c, kind, owner, nid, actor or owner, db_id=dbid)


def _on_assigned(c, owner, note_id, pid, people, actor=None, **_):
    _emit(c, "row.assigned", owner, note_id, actor, [pid], db_id=_db_of(c, owner, note_id))


def _on_comment(c, note_id, owner, author=None, **_):
    if owner and not is_remote(owner):
        _emit(c, "comment.created", owner, note_id, author)


def _on_task(kind):
    def fn(c, uid, task_id, **_):
        if not conf().get("webhooks"):
            return
        now = time.time()
        for hid, huid, _r in _hooks_for(c, kind):
            if huid == uid:
                eid = "evt_" + uuid.uuid4().hex
                c.execute("INSERT INTO webhook_delivery (hook_id, event_id, kind, note_id, actor, changed, created, next_try) "
                          "VALUES (?,?,?,?,?,?,?,?)", (hid, eid, kind, task_id, uid, "[]", now, now))
                c.after_commit(_wake.set)
    fn.__name__ = "_on_" + kind.replace(".", "_")
    fn.__qualname__ = fn.__name__
    return fn


bus.on("page.content", _on_content)
bus.on("row.changed", _on_row)
bus.on("notes.tree", _on_tree)
bus.on("row.assigned", _on_assigned)
bus.on("comment.added", _on_comment)
bus.on("task.created", _on_task("task.created"))
bus.on("task.completed", _on_task("task.completed"))


# --- Zustellung --------------------------------------------------------------------------------------------------
def sign(secret, ts, body):
    return hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()


def _payload(c, d, hook_uid, roots):
    """-> (body-bytes, None) oder (None, grund) - Rechte JETZT pruefen."""
    _id, eid, kind, nid, dbid, actor, changed, created = d
    data = {}
    if kind.startswith(("page.", "row.", "comment.")):
        acc = Access(c, hook_uid)
        gone = kind in ("page.trashed", "row.deleted")
        role = acc.role(nid) if nid else None
        owner = acc.owner(nid) if nid else None
        if gone:
            # im Papierkorb: Leserecht ist weg - nur der Eigentuemer bekommt die Meldung (Titel), andere nur die ID
            if owner != hook_uid:
                return None, "no access"
        elif not role:
            return None, "no access"
        if roots and owner and not apitok.in_roots(c, apitok.Tok(0, hook_uid, "", "", (), roots, None), owner, nid) and not gone:
            return None, "outside roots"
        r = c.execute("SELECT title FROM note WHERE id=?", (nid,)).fetchone()
        data.update(note_id=nid, title=(r[0] or "") if r else "")
        if dbid:
            data["db_id"] = dbid
        if kind.startswith("row.") or changed not in (None, "[]"):
            data["changed"] = _json_load(changed, [])
    elif kind.startswith("task."):
        data["task_id"] = nid
    if actor:
        nm = names(c, [actor])
        data["actor"] = {"handle": actor if is_remote(actor) else local_ref(actor), "name": nm.get(actor) or ""}
    body = json.dumps({"id": eid, "type": kind, "created": created, "data": data}, ensure_ascii=False, separators=(",", ":")).encode()
    return body, None


def _hook_ok(c, h):
    """Hook darf jetzt senden? -> None oder Grund (dann still verwerfen bzw. Hook abschalten)."""
    cf = conf()
    if not cf.get("webhooks"):
        return "disabled"
    if not apitok.allowed_for(user_info(h["uid"]), cf):
        return "account"
    if h["token_id"] is not None and not _token_ok(c, h["token_id"]):
        c.execute("UPDATE webhook SET active=0, last_error='token revoked' WHERE id=?", (h["id"],))
        return "token"
    return None


def _send(url, headers, body):
    """-> (status-code oder None, fehlertext)."""
    u, err = check_url(url)
    if err:
        return None, err
    try:
        with netguard.guarded_client(_ip_ok, timeout=httpx.Timeout(TIMEOUT), follow_redirects=False) as cl:
            with cl.stream("POST", u, headers=headers, content=body) as r:
                n = 0
                for chunk in r.iter_bytes():
                    n += len(chunk)
                    if n >= READ_MAX:
                        break
                code = r.status_code
    except netguard.Blocked:
        return None, t("Ziel nicht erlaubt (interne Adresse)")
    except httpx.ConnectError as ex:
        return None, t("Ziel nicht erlaubt (interne Adresse)") if isinstance(ex.__cause__, netguard.Blocked) or "nicht freigegeben" in str(ex) \
            or "RESTORE_TEST" in str(ex) else netguard.net_why(ex)
    except httpx.TimeoutException:
        return None, t("Zeitüberschreitung")
    except (httpx.HTTPError, OSError) as ex:
        return None, netguard.net_why(ex)
    if 200 <= code < 300:
        return code, ""
    if 300 <= code < 400:
        return code, t("Weiterleitung – wird nicht verfolgt")
    return code, f"HTTP {code}"


def deliver(did):
    """Eine Zustellung (gesperrt durch claim)."""
    with _db() as c:
        d = c.execute("SELECT id, event_id, kind, note_id, db_id, actor, changed, created, hook_id, attempts FROM webhook_delivery WHERE id=?",
                      (did,)).fetchone()
        if not d:
            return
        h = c.execute("SELECT id, uid, url, secret_enc, roots, token_id, active, fail_streak FROM webhook WHERE id=?", (d[8],)).fetchone()
    if not h:
        return
    h = {"id": h[0], "uid": h[1], "url": h[2], "secret": auth._dec(h[3]), "roots": apitok.roots_load(h[4]),
         "token_id": h[5], "active": h[6], "fail_streak": h[7]}
    now = time.time()
    with _db() as c, c:
        why = None if h["active"] else "inactive"
        why = why or _hook_ok(c, h)
        body, reason = (None, why) if why else _payload(c, d[:8], h["uid"], h["roots"])
        if body is None:
            c.execute("UPDATE webhook_delivery SET status='dropped', last_error=?, done=?, locked_until=NULL WHERE id=?", (reason or "", now, did))
            return
    ts = int(time.time())
    headers = {"Content-Type": "application/json", "User-Agent": f"Saga-Webhooks/{VERSION}", "Saga-Event": d[2], "Saga-Delivery": d[1],
               "Saga-Signature": f"t={ts},v1={sign(h['secret'], ts, body)}"}
    code, err = _send(h["url"], headers, body)
    now = time.time()
    with _db() as c, c:
        att = d[9] + 1
        if not err:
            c.execute("UPDATE webhook_delivery SET status='ok', attempts=?, last_code=?, last_error='', done=?, locked_until=NULL WHERE id=?",
                      (att, code, now, did))
            c.execute("UPDATE webhook SET fail_streak=0, last_ok=?, last_error='' WHERE id=?", (now, h["id"]))
        else:
            final = att > len(RETRY)
            c.execute("UPDATE webhook_delivery SET status=?, attempts=?, last_code=?, last_error=?, next_try=?, done=?, locked_until=NULL WHERE id=?",
                      ("failed" if final else "pending", att, code, err[:200], None if final else now + RETRY[att - 1], now if final else None, did))
            streak = c.execute("UPDATE webhook SET fail_streak=fail_streak+1, last_error=? WHERE id=? RETURNING fail_streak",
                               (err[:200], h["id"])).fetchone()
            if streak and streak[0] >= FAIL_OFF:
                n = c.execute("UPDATE webhook SET active=0 WHERE id=? AND active=1", (h["id"],)).rowcount
                if n:
                    notify.create(c, h["uid"], "webhook.disabled", None, None, data={"hook": h["id"]})
                    print(f"webhooks: Hook {h['id']} nach {FAIL_OFF} Fehlschlaegen abgeschaltet", flush=True)
        # Protokoll kurz halten
        c.execute("DELETE FROM webhook_delivery WHERE hook_id=? AND status<>'pending' AND id NOT IN "
                  "(SELECT id FROM webhook_delivery WHERE hook_id=? ORDER BY id DESC LIMIT ?)", (h["id"], h["id"], LOG_KEEP))


def claim(n=20):
    now = time.time()
    with _db() as c, c:
        ids = [r[0] for r in c.execute("SELECT id FROM webhook_delivery WHERE status='pending' AND next_try<=? AND (locked_until IS NULL OR locked_until<?) "
                                       "ORDER BY next_try LIMIT ? FOR UPDATE SKIP LOCKED", (now, now, n)).fetchall()]
        if ids:
            c.execute("UPDATE webhook_delivery SET locked_until=? WHERE id = ANY(?)", (now + 60, ids))
    return ids


def next_due():
    with _db() as c:
        r = c.execute("SELECT MIN(next_try) FROM webhook_delivery WHERE status='pending'").fetchone()
    return r[0] if r and r[0] is not None else None


_worker = {"thread": None, "stop": False}


def _loop():
    pool = ThreadPoolExecutor(PARALLEL, thread_name_prefix="saga-webhook")
    while not _worker["stop"]:
        try:
            if conf().get("webhooks"):
                ids = claim()
                if ids:
                    list(pool.map(_safe_deliver, ids))
                    continue
                nd = next_due()
                wait = 30.0 if nd is None else max(0.2, min(30.0, nd - time.time()))
            else:
                wait = 30.0
        except Exception as ex:  # noqa: BLE001 - Datenbank kurz weg o. ae.: naechster Durchlauf
            print(f"webhooks: {type(ex).__name__}: {ex}", flush=True)
            wait = 5.0
        _wake.wait(wait)
        _wake.clear()
    pool.shutdown(wait=False)


def _safe_deliver(did):
    try:
        deliver(did)
    except Exception as ex:  # noqa: BLE001 - eine kaputte Zustellung haelt die anderen nicht auf
        print(f"webhooks: Zustellung {did}: {type(ex).__name__}: {ex}", flush=True)


def start():
    if RESTORE_TEST:
        return
    if _worker["thread"] is None:
        _worker["stop"] = False
        th = threading.Thread(target=_loop, name="saga-webhooks", daemon=True)
        _worker["thread"] = th
        th.start()


def stop():
    _worker["stop"] = True
    _wake.set()
    _worker["thread"] = None


def _notif(c, uid, item, d):
    item["access"] = True
    item["system"] = True
    item["title"] = t("Webhook abgeschaltet")
    item["text"] = t("Ein Webhook wurde nach {n} fehlgeschlagenen Zustellungen in Folge abgeschaltet. Adresse prüfen und unter Einstellungen → API wieder einschalten.", n=FAIL_OFF)
    item["url"] = "/notes?settings=api"


notify.RENDER["webhook.disabled"] = _notif


# --- Endpunkte ---------------------------------------------------------------------------------------------------
def _ns(d, status=200):
    return JSONResponse(d, status_code=status, headers={"Cache-Control": "no-store"})


def _int_id(hid):
    return 0 < hid < 2 ** 63


@router.get("/api/account/webhooks")
def api_hooks(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    cf = conf()
    return _ns({"enabled": bool(cf.get("webhooks")) and apitok.allowed_for(user_info(uid), cf), "events": list(EVENTS),
                "http": bool(cf.get("webhook_http")), "max": HOOKS_MAX, "hooks": list_hooks(uid, True)})


@router.post("/api/account/webhooks")
async def api_hook_add(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request)
    if b is None:
        return _bad(t("ungültige Anfrage"))
    if not apitok._confirmed(request, uid):
        return apitok._need_confirm(uid)
    out, err = await run_in_threadpool(create_hook, uid, b)
    return _bad(err[1], err[0]) if err else _ns(out)


@router.patch("/api/account/webhooks/{hid}")
async def api_hook_patch(hid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request)
    if b is None or not _int_id(hid):
        return _bad(t("ungültige Anfrage"))
    st, d = await run_in_threadpool(update_hook, uid, hid, b)
    return _ns(d, st)


@router.delete("/api/account/webhooks/{hid}")
def api_hook_delete(hid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    return _ns({"ok": True}) if _int_id(hid) and delete_hook(uid, hid) else _bad("not found", 404)


@router.post("/api/account/webhooks/{hid}/test")
def api_hook_test(hid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    eid = ping(uid, hid) if _int_id(hid) else None
    return _ns({"ok": True, "event": eid}) if eid else _bad("not found", 404)


@router.post("/api/account/webhooks/{hid}/rotate")
def api_hook_rotate(hid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    if not apitok._confirmed(request, uid):
        return apitok._need_confirm(uid)
    s = rotate(uid, hid) if _int_id(hid) else None
    return _ns({"secret": s}) if s else _bad("not found", 404)


def _v1(request, write=False):
    tok, err = apitok.authed(request, write=write, scope="webhooks")
    if err is None and not conf().get("webhooks"):
        err = JSONResponse({"error": t("Webhooks sind auf diesem Server abgeschaltet."), "code": "disabled"}, status_code=403)
    return tok, err


@router.get("/api/v1/webhooks")
def v1_hooks(request: Request):
    tok, err = _v1(request)
    return err or _ns({"webhooks": list_hooks(tok.uid), "events": list(EVENTS)})


@router.post("/api/v1/webhooks")
async def v1_hook_add(request: Request):
    tok, err = _v1(request, True)
    if err:
        return err
    b = await _body(request)
    if b is None:
        return _ns({"error": t("ungültige Anfrage"), "code": "bad_request"}, 400)
    out, e = await run_in_threadpool(create_hook, tok.uid, b, tok)
    return _ns({"error": e[1], "code": "bad_request" if e[0] == 400 else "forbidden" if e[0] == 403 else "conflict"}, e[0]) if e else _ns(out, 201)


@router.delete("/api/v1/webhooks/{hid}")
def v1_hook_delete(hid: int, request: Request):
    tok, err = _v1(request, True)
    if err:
        return err
    return _ns({"ok": True}) if _int_id(hid) and delete_hook(tok.uid, hid) else _ns({"error": "not found", "code": "not_found"}, 404)


@router.get("/api/v1/webhooks/{hid}/deliveries")
def v1_hook_log(hid: int, request: Request):
    tok, err = _v1(request)
    if err:
        return err
    with _db() as c:
        if not _int_id(hid) or not c.execute("SELECT 1 FROM webhook WHERE id=? AND uid=?", (hid, tok.uid)).fetchone():
            return _ns({"error": "not found", "code": "not_found"}, 404)
        return _ns({"deliveries": _log(c, hid)})


@router.post("/api/v1/webhooks/{hid}/test")
def v1_hook_test(hid: int, request: Request):
    tok, err = _v1(request, True)
    if err:
        return err
    eid = ping(tok.uid, hid) if _int_id(hid) else None
    return _ns({"ok": True, "event": eid}) if eid else _ns({"error": "not found", "code": "not_found"}, 404)
