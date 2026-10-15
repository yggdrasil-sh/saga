"""Live-Kanal (1.4): Server-Sent Events unter GET /api/events, Zustellung nur an Berechtigte, Anwesenheit.

Kein zusaetzlicher Port: alles laeuft ueber den normalen Web-Port hinter dem Reverse Proxy.

Verteilung: Saga laeuft als EIN Prozess (run.py: Hauptseite und Admin-Bereich als zwei uvicorn-Server in derselben
Event-Schleife, je ein Worker). Sperren, Einstellungen, Ratenbegrenzung und Zwischenspeicher liegen im Speicher dieses
Prozesses - mehrere Worker sind nicht vorgesehen. Deshalb ein In-Process-Bus (saga.bus.emit -> Warteschlange hier);
die Schnittstelle (emit(kind, payload) aus jedem Thread) passt auch fuer PostgreSQL LISTEN/NOTIFY, falls Saga einmal
mehrere Prozesse bekommt. Ein Neustart der Datenbank stoert den Kanal nicht: Verbindungen bleiben offen, nur die
Rechtepruefung einzelner Ereignisse schlaegt waehrenddessen fehl (Ereignis entfaellt, der Client gleicht beim
naechsten Laden ab).

Ereignisse (event: ..., data: JSON) - die Rechte werden beim VERSAND geprueft (sharing.Access):
  hello            {boot, ping, presence_ttl}         gleich nach dem Verbinden (ohne id)
  notification     {item, unread, replaces} | {unread, read}   neue Benachrichtigung / gelesen (andere Tabs)
  note.changed     {id, rev, by:{name, username}, updated, gone}   an alle, die die Seite gerade offen haben (Anwesenheit)
  comments.changed {note_id, counts:{blocks, open, unread}}   an alle mit Zugriff (Zahlen je Empfaenger)
  shared.changed   {}                                  "Mit mir geteilt" neu laden
  presence         {note_id, users:[{username, name, editing}]}
  reset            {}                                  Wiederaufnahme nicht moeglich (Neustart/zu alt): alles neu laden
  bye              {reason: limit|auth|account|shutdown}   Server beendet die Verbindung
  chat.message     {chat_id, seq, id, sender}          Chat (1.5), an die Mitglieder - nie Inhalt
  chat.deleted     {chat_id, id, seq} · chat.read {chat_id, seq} (eigene Tabs) · chat.typing {chat_id, user}
  chat.changed     {chat_id} (Mitglieder/Name/Frist) · chat.link {id, approved?, gone?} · chat.device {id, revoked}
Jedes Ereignis ausser hello/reset/bye hat eine id (<boot>-<nummer>); mit Last-Event-ID holt ein neuer Aufbau verpasste
Ereignisse der letzten 10 Minuten nach. Heartbeat: Kommentarzeile alle ~25 s (haelt Proxys offen).
Hoechstens 5 Verbindungen je Konto - die aelteste wird mit "bye" (limit) geschlossen."""
import asyncio
import json
import secrets
import time
from collections import deque
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from db import connect as _db
from saga import bus, i18n, notify
from saga.comments import counts as comment_counts
from saga.core import E, _api_guard, _bad, _body, _session_user, _tok_hash, account_lang, device_alive, origin_of, user_info
from saga.notes import note_chain
from saga.sharing import Access, names

router = APIRouter()

PING = max(2, int(E.get("LIVE_PING", "25")))                  # Sekunden ohne Ereignis bis zur Heartbeat-Zeile
MAX_PER_USER = max(1, int(E.get("LIVE_MAX_PER_USER", "5")))
MAX_TOTAL = max(10, int(E.get("LIVE_MAX_TOTAL", "2000")))
PRESENCE_TTL = 45                                             # Anwesenheit verfaellt ohne Meldung nach 45 s
PRESENCE_MAX = 20                                             # Seiten je Konto gleichzeitig
REPLAY_SEC, REPLAY_MAX = 600, 300                             # Last-Event-ID: 10 Minuten, hoechstens 300 Ereignisse je Konto
MAX_AGE = max(60, int(E.get("LIVE_MAX_AGE", "3600")))         # nach einer Stunde "bye/refresh": der Client verbindet neu
LINGER = 180                                                  # nach dem Trennen noch 3 min fuer die Wiederaufnahme sammeln
QUEUE_MAX = 20000                                             # Verteiler-Warteschlange (alle Ereignisse)
QUEUE = 256                                                   # Ereignisse je Verbindung im Puffer (langsamer Client -> trennen)
BOOT = secrets.token_hex(4)
HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Content-Type-Options": "nosniff"}

_S = {"loop": None, "q": None, "tasks": [], "seq": 0, "floor": 0}   # floor: aeltere Nummern nie nachholen (_lost geleert)
_clients = {}            # uid -> [_Client]                  (nur in der Event-Schleife veraendert)
_left = {}               # uid -> Zeitpunkt, an dem die letzte Verbindung endete
_buf = {}                # uid -> deque[(seq, zeit, nachricht, seite|None)]
_lost = {}               # uid -> hoechste Nummer, die fuer dieses Konto NICHT mehr nachgeholt werden kann
_presence = {}           # note_id -> {uid: [zeit, bearbeitet]}
_pres_hits = {}          # uid -> letzte Anwesenheitsmeldung (Bremse)


class _Client:
    __slots__ = ("uid", "kind", "q", "bye", "since", "origin")

    def __init__(self, uid, kind, origin=None):
        self.uid, self.kind, self.q, self.bye, self.since = uid, kind, asyncio.Queue(QUEUE), None, time.time()
        self.origin = origin             # Sitzung/Geraet (core.origin_of): eigene Aenderungen nicht an dieselbe Stelle zurueck

    def put(self, msg):
        try:
            self.q.put_nowait(msg)
        except asyncio.QueueFull:
            self.close("slow")

    def close(self, reason):
        if self.bye is None:
            self.bye = reason
        while True:                        # Platz fuer das Ende-Zeichen schaffen
            try:
                self.q.put_nowait(None)
                return
            except asyncio.QueueFull:
                try:
                    self.q.get_nowait()
                except asyncio.QueueEmpty:
                    pass


def _fmt(typ, data, seq=None):
    head = f"id: {BOOT}-{seq}\n" if seq is not None else ""
    return f"{head}event: {typ}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


# --- Start/Stopp (app.py lifespan) ------------------------------------------------------------------------
def start():
    if _S["loop"] is not None:
        return
    loop = asyncio.get_running_loop()
    _S["loop"], _S["q"] = loop, asyncio.Queue(QUEUE_MAX)     # begrenzt (BUG-043): bei Stau fallen Ereignisse weg
    bus.set_sink(_sink)
    _S["tasks"] = [asyncio.create_task(_dispatch()), asyncio.create_task(_housekeeping())]


def stop():
    bus.set_sink(None)
    for tk in _S["tasks"]:
        tk.cancel()
    for lst in list(_clients.values()):
        for cl in list(lst):
            cl.close("shutdown")
    _S.update(loop=None, q=None, tasks=[])


def count():
    """Aktive Live-Verbindungen (Admin-Bereich, nur die Zahl)."""
    return sum(len(v) for v in list(_clients.values()))


def _sink(kind, payload):
    loop, q = _S["loop"], _S["q"]
    if loop is None or q is None or loop.is_closed():
        return
    loop.call_soon_threadsafe(_put, q, (kind, payload))


def _put(q, item):
    try:
        q.put_nowait(item)
    except asyncio.QueueFull:                # Stau: Ereignis verwerfen (Clients gleichen beim naechsten Laden/Abfragen ab)
        _S["dropped"] = _S.get("dropped", 0) + 1
        if _S["dropped"] % 1000 == 1:
            print(f"live: Warteschlange voll - {_S['dropped']} Ereignisse verworfen", flush=True)


# --- Zustellung -------------------------------------------------------------------------------------------
def _audience():
    now = time.time()
    return set(_clients) | {u for u, ts in _left.items() if now - ts < LINGER}


def _note_of(typ, data):
    """Seite, an die ein Ereignis gebunden ist - beim Nachholen (Last-Event-ID) wird der Zugriff darauf erneut geprueft.
    None: nicht seitengebunden (shared.changed, gelesen) oder ohne Inhalt (geloescht/"gone")."""
    if typ == "note.changed":
        return None if data.get("gone") else data.get("id")
    if typ in ("comments.changed", "presence"):
        return data.get("note_id")
    if typ == "notification":
        return ((data.get("item") or {}).get("note") or {}).get("id")
    return None


def _deliver(uid, typ, data, skip=None):
    """skip: Herkunft (Sitzung/Geraet), die das Ereignis selbst ausgeloest hat - deren Verbindungen bekommen es nicht."""
    _S["seq"] += 1
    seq = _S["seq"]
    msg = _fmt(typ, data, seq)
    buf = _buf.get(uid)
    if buf is None:
        buf = _buf[uid] = deque()
    buf.append((seq, time.time(), msg, _note_of(typ, data), skip))
    while len(buf) > REPLAY_MAX:
        _lost[uid] = max(_lost.get(uid, 0), buf.popleft()[0])
    for cl in list(_clients.get(uid, ())):
        if skip is None or cl.origin != skip:
            cl.put(msg)


async def _dispatch():
    q = _S["q"]
    while True:
        kind, payload = await q.get()
        try:
            if kind == "account.deleted":
                _drop_user(payload.get("uid"))
                continue
            aud = _audience()
            if not aud:
                continue
            pres = {k: {u: bool(e[1]) for u, e in v.items()} for k, v in _presence.items()}     # Kopie fuer den Thread
            out = await asyncio.to_thread(_resolve, kind, payload, aud, pres)
            for item in out:
                uid, typ, data = item[:3]
                if typ == "_unpresent":                       # Zugriff verloren: aus der Anwesenheit nehmen
                    if _presence.get(data, {}).pop(uid, None) is not None and not _presence.get(data):
                        _presence.pop(data, None)
                    continue
                _deliver(uid, typ, data, item[3] if len(item) > 3 else None)
        except Exception as ex:  # noqa: BLE001 - z. B. Datenbank gerade weg: dieses Ereignis entfaellt, der Kanal bleibt
            print(f"live: {kind}: {type(ex).__name__}: {ex}", flush=True)


def _person(nm, u):
    return {"name": nm.get(u) or u, "username": u}


def _page_audience(c, nid, aud):
    """Verbundene Konten, die die Seite ueberhaupt sehen KOENNTEN (Eigentuemer + Freigaben in der Kette) - statt jedes
    verbundene Konto einzeln zu pruefen (BUG-043). Die Rechte prueft der Aufrufer danach je Konto."""
    r = c.execute("SELECT uid FROM note WHERE id=?", (nid,)).fetchone()
    if not r:
        return set()
    chain = note_chain(c, r[0], nid) or [nid]
    cand = {r[0]}
    for tt, tg in c.execute(f"SELECT target_type, target FROM note_share WHERE owner_uid=? AND note_id IN ({','.join('?' * len(chain))})",
                            (r[0], *chain)).fetchall():
        if tt == "user":
            cand.add(tg)
        elif tt == "group":
            cand.update(notify.group_members(c, tg))
    return cand & aud


def _resolve(kind, p, aud, pres):
    """Im Thread: Empfaenger und Inhalt je Empfaenger bestimmen -> [(uid, ereignis, daten)]. Rechte JETZT pruefen."""
    out = []
    with _db() as c:
        if kind == "notification":
            uid = p["uid"]
            if uid in aud:
                i18n.set_lang(account_lang(uid))             # 'text' in der Sprache des Empfaengers (eigener Thread-Kontext)
                it = notify.item(c, uid, p["id"])
                if it:
                    out.append((uid, "notification", {"item": it, "unread": notify.count_unread(c, uid), "replaces": p.get("replaces")}))
        elif kind == "direct":                               # Chat (1.5): Empfaenger stehen fest (Mitglieder), nur Kennungen
            for u in p.get("uids") or ():
                if u in aud:
                    out.append((u, p["typ"], p["data"]))
        elif kind == "notification.read":
            if p["uid"] in aud:
                out.append((p["uid"], "notification", {"unread": p["unread"], "read": p["ids"]}))
        elif kind == "note.changed":
            nid, by, origin = p["id"], p.get("by"), p.get("origin")
            nm = names(c, [by]) if by else {}
            # Der Autor bekommt die Aenderung auf seinen ANDEREN Geraeten/Sitzungen (BUG-032) - nur die ausloesende
            # Sitzung nicht (ohne bekannte Herkunft wie bisher gar nicht)
            for u in sorted((set(pres.get(nid, {})) & aud) - (set() if origin else {by})):
                skip = origin if u == by else None
                if p.get("gone"):              # geloescht/archiviert: Zugriff hat jetzt niemand mehr - nur "weg", ohne Namen
                    out.append((u, "note.changed", {"id": nid, "rev": None, "updated": p.get("updated"), "gone": True, "by": None}, skip))
                elif Access(c, u).role(nid):
                    out.append((u, "note.changed", {"id": nid, "rev": p.get("rev"), "updated": p.get("updated"), "gone": False,
                                                    "by": _person(nm, by) if by else None}, skip))
        elif kind == "comments.changed":
            nid = p["note_id"]
            if not c.execute("SELECT 1 FROM note WHERE id=? AND deleted_at IS NULL", (nid,)).fetchone():
                return out
            who = {p["only"]} & aud if p.get("only") else _page_audience(c, nid, aud)
            for u in sorted(who):
                if Access(c, u).role(nid):
                    per, n_open, n_unread = comment_counts(c, u, nid)
                    out.append((u, "comments.changed", {"note_id": nid, "counts": {"blocks": per, "open": n_open, "unread": n_unread}}))
        elif kind == "shared.changed":
            if p.get("target_type") == "user":
                who = {p.get("target")} & aud
            else:                                                # nur Mitglieder der Gruppe, nicht jedes Konto pruefen (BUG-043)
                who = set(notify.group_members(c, p.get("target"))) & aud if p.get("target") else set()
            for u in sorted(who - {p.get("owner")}):
                out.append((u, "shared.changed", {}))
        elif kind == "presence":
            nid = p["note_id"]
            entries = pres.get(nid, {})
            here = sorted(entries)
            ok = [u for u in here if Access(c, u).role(nid)]
            out += [(u, "_unpresent", nid) for u in here if u not in ok]
            nm = names(c, ok)
            users = [dict(_person(nm, u), editing=entries[u]) for u in ok]
            for u in ok:
                if u in aud:
                    out.append((u, "presence", {"note_id": nid, "users": [dict(x, me=x["username"] == u) for x in users]}))
    return out


def _drop_user(uid):
    """Konto geloescht: Verbindungen schliessen, Anwesenheit und Puffer weg."""
    if not uid:
        return
    for cl in list(_clients.get(uid, ())):
        cl.close("account")
    _buf.pop(uid, None)
    _left.pop(uid, None)
    _lost[uid] = _S["seq"]
    for nid in [n for n, v in _presence.items() if uid in v]:
        _presence[nid].pop(uid, None)
        if not _presence[nid]:
            _presence.pop(nid, None)
        _put(_S["q"], ("presence", {"note_id": nid}))


async def _housekeeping():
    """Alle 5 s: abgelaufene Anwesenheit (-> Ereignis), alte Puffer, Konten ohne Verbindung nach LINGER."""
    while True:
        await asyncio.sleep(5)
        try:
            now = time.time()
            for nid in list(_presence):
                v = _presence[nid]
                gone = [u for u, (ts, _) in v.items() if now - ts > PRESENCE_TTL]
                for u in gone:
                    v.pop(u, None)
                if not v:
                    _presence.pop(nid, None)
                if gone:
                    _put(_S["q"], ("presence", {"note_id": nid}))
            for uid, buf in list(_buf.items()):
                while buf and now - buf[0][1] > REPLAY_SEC:
                    _lost[uid] = max(_lost.get(uid, 0), buf.popleft()[0])
            for uid, ts in list(_left.items()):
                if now - ts >= LINGER and not _clients.get(uid):
                    _left.pop(uid, None)
                    _buf.pop(uid, None)
                    _lost[uid] = _S["seq"]
            if len(_lost) > 20000:
                _lost.clear()                                  # Folge: Wiederaufnahme aelterer Nummern -> "reset" (sicher)
                _S["floor"] = _S["seq"]
            for uid, ts in list(_pres_hits.items()):
                if now - ts > 60:
                    _pres_hits.pop(uid, None)
        except Exception as ex:  # noqa: BLE001
            print(f"live: housekeeping: {type(ex).__name__}: {ex}", flush=True)


# --- Verbindungen ----------------------------------------------------------------------------------------
def _register(uid, kind, origin=None):
    lst = _clients.setdefault(uid, [])
    while len(lst) >= MAX_PER_USER:
        lst.pop(0).close("limit")
    cl = _Client(uid, kind, origin)
    lst.append(cl)
    _left.pop(uid, None)
    return cl


def _unregister(cl):
    lst = _clients.get(cl.uid)
    if lst and cl in lst:
        lst.remove(cl)
    if not lst:
        _clients.pop(cl.uid, None)
        if cl.bye != "account":
            _left[cl.uid] = time.time()


def _replay(uid, last, origin=None):
    """Last-Event-ID '<boot>-<n>' -> ([(seite|None, nachricht)], ok). ok=False: nicht vollstaendig nachholbar -> 'reset'."""
    try:
        boot, n = str(last).strip().split("-", 1)
        n = int(n)
    except ValueError:
        return [], False
    if boot != BOOT or n > _S["seq"] or n < max(_lost.get(uid, 0), _S["floor"]):
        return [], False
    return [(nid, m) for s, _, m, nid, skip in _buf.get(uid, ()) if s > n and (skip is None or skip != origin)], True


def _allowed(uid, nids):
    """Im Thread: welche dieser Seiten darf uid JETZT sehen? (Nachholen nach Entzug: nichts mehr zu dieser Seite)"""
    with _db() as c:
        acc = Access(c, uid)
        return {n for n in nids if acc.role(n)}


def _who(request):
    """-> (uid, art, pruefung) - Sitzung (Web, Apps nach /app/resume) oder Geraete-Schluessel 'Authorization: Bearer hkd_...'."""
    hdr = request.headers.get("authorization", "")
    if hdr.startswith("Bearer hkd_") and len(hdr) <= 130:
        h = _tok_hash(hdr[7:].strip())

        def dev_ok(want_id=False):
            with _db() as c:
                r = c.execute("SELECT uid, id FROM device_login WHERE hash=?", (h,)).fetchone()
            uid_ = r[0] if r and user_info(r[0]) is not None else None
            return (uid_, r[1] if uid_ else None) if want_id else uid_
        uid, did = dev_ok(True)
        return (uid, "app", lambda: dev_ok() == uid, f"d{did}") if uid else None
    uid = _session_user(request)
    if not uid:
        return None
    pwc = float(request.session.get("pwc") or 0)

    def ok():
        info = user_info(uid)
        return info is not None and info["epoch"] <= pwc and (did is None or device_alive(uid, did))
    did = request.session.get("did")
    return uid, "web", ok, origin_of(request)


@router.get("/api/events")
async def api_events(request: Request):
    """Server-Sent Events (text/event-stream). Anmeldung: Sitzung oder Geraete-Schluessel. Siehe Kopf dieser Datei."""
    who = await asyncio.to_thread(_who, request)
    if not who:
        return _bad("unauthorized", 401)
    if _S["loop"] is None:
        return _bad("live channel not running", 503)
    if count() >= MAX_TOTAL:
        return JSONResponse({"error": "too many connections"}, status_code=503, headers={"Retry-After": "60"})
    uid, kind, check, origin = who
    # EventSource kann keinen X-Saga-Kopf setzen: Anfragen anderer Seiten (auch Nachbar-Subdomains, SameSite=Lax schickt
    # dort das Cookie mit) abweisen - sie koennten sonst die eigenen Verbindungen ueber die Grenze hinausdraengen
    if kind == "web" and request.headers.get("sec-fetch-site") in ("cross-site", "same-site"):
        return _bad("forbidden", 403)
    last = request.headers.get("last-event-id") or request.query_params.get("last") or ""

    async def gen():
        # erst hier anmelden: startet der Generator nie (Client sofort weg), bliebe sonst ein Eintrag ohne finally liegen.
        # Anmelden und Nachhol-Stand ohne await dazwischen: nichts doppelt, nichts verloren
        cl = _register(uid, kind, origin)
        msgs, ok = _replay(uid, last, origin) if last else ([], True)
        try:
            # retry: Wartezeit vor dem Neuaufbau; Fuellzeile: manche Proxys geben erst ab ein paar KB weiter
            yield "retry: 5000\n:" + " " * 2048 + "\n\n"
            yield _fmt("hello", {"boot": BOOT, "ping": PING, "presence_ttl": PRESENCE_TTL})
            nids = {n for n, _ in msgs if n}
            if nids:                                              # Rechte beim Nachholen JETZT pruefen (Entzug seitdem)
                try:
                    allowed = await asyncio.to_thread(_allowed, uid, nids)
                    msgs = [(n, m) for n, m in msgs if not n or n in allowed]
                except Exception:  # noqa: BLE001 - Datenbank gerade weg: nichts nachholen, Client gleicht ab
                    msgs, ok = [], False
            if not ok:
                yield _fmt("reset", {})
            for _, m in msgs:
                yield m
            checked = born = time.monotonic()
            while True:
                if time.monotonic() - born > MAX_AGE:             # Sitzung beim Neuaufbau frisch pruefen (Cookie-Ablauf)
                    yield _fmt("bye", {"reason": "refresh"})
                    return
                try:
                    msg = await asyncio.wait_for(cl.q.get(), timeout=PING)
                except asyncio.TimeoutError:
                    msg = ""
                if msg is None:
                    yield _fmt("bye", {"reason": cl.bye or "closed"})
                    return
                if msg:
                    yield msg
                if time.monotonic() - checked >= PING:
                    checked = time.monotonic()
                    try:
                        valid = await asyncio.to_thread(check)
                    except Exception:  # noqa: BLE001 - Datenbank gerade weg: Verbindung halten, spaeter erneut pruefen
                        valid = True
                    if not valid:                                 # gesperrt, abgemeldet, Passwort geaendert, Geraet entfernt
                        cl.bye = "auth"
                        yield _fmt("bye", {"reason": "auth"})
                        return
                    if not msg:
                        yield f": ping {int(time.time())}\n\n"
        finally:
            _unregister(cl)

    return StreamingResponse(gen(), media_type="text/event-stream; charset=utf-8", headers=HEADERS)


# --- Anwesenheit ----------------------------------------------------------------------------------------
def _here(c, uid, nid, entries):
    """Im Thread: darf uid die Seite sehen? -> (zustand der Seite, liste der Anwesenden mit Zugriff) oder None.
    entries: Kopie der Anwesenheit dieser Seite (aus der Event-Schleife)."""
    acc = Access(c, uid)
    if not acc.role(nid):
        return None
    r = c.execute("SELECT rev, updated, updated_by FROM note WHERE id=? AND deleted_at IS NULL", (nid,)).fetchone()
    if not r:
        return None
    ok = [u for u in sorted(entries) if u == uid or Access(c, u).role(nid)]
    nm = names(c, ok + ([r[2]] if r[2] else []))
    users = [dict(_person(nm, u), editing=bool(entries[u][1])) for u in ok if u in entries]
    by = _person(nm, r[2]) if r[2] and r[2] != uid else None
    return {"rev": r[0], "updated": r[1], "by": by}, users


@router.post("/api/presence")
async def api_presence(request: Request):
    """Seite offen: {note_id, editing} alle ~20 s; {note_id, leave: true} beim Verlassen. -> {users, rev, updated, by, ttl, live}
    (rev/updated: Stand der Seite - ohne Live-Kanal erkennt der Client so fremde Aenderungen)."""
    uid = await asyncio.to_thread(_api_guard, request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request)
    if b is None:
        return _bad("bad request")
    raw_id = b.get("note_id")                                # nur ganze Zahl (auch als Text) - nicht 1.5/true (BUG-083)
    if isinstance(raw_id, bool) or not (isinstance(raw_id, int) or (isinstance(raw_id, str) and raw_id.isdigit())):
        return _bad("bad request")
    nid = int(raw_id)
    if not 0 < nid < 2 ** 63:
        return _bad("bad request")
    q = _S["q"]
    if b.get("leave"):
        v = _presence.get(nid)
        if v and v.pop(uid, None) is not None:
            if not v:
                _presence.pop(nid, None)
            if q is not None:
                _put(q, ("presence", {"note_id": nid}))
        return JSONResponse({"ok": True})
    now = time.time()
    if now - _pres_hits.get((uid, nid), 0) < 1:              # mehrere Tabs/schnelle Folge: nichts tun, kein Fehler
        return JSONResponse({"skipped": True}, headers={"Cache-Control": "no-store"})
    _pres_hits[(uid, nid)] = now
    entries = {u: list(e) for u, e in _presence.get(nid, {}).items()}
    out = await asyncio.to_thread(_here_db, uid, nid, entries)
    if out is None:
        v = _presence.get(nid)
        if v and v.pop(uid, None) is not None and q is not None:
            _put(q, ("presence", {"note_id": nid}))
        return _bad("not found", 404)
    editing = b.get("editing") is True
    v = _presence.setdefault(nid, {})
    old = v.get(uid)
    v[uid] = [now, editing]
    mine = [n for n, e in _presence.items() if uid in e]
    if len(mine) > PRESENCE_MAX:                             # zu viele Seiten gleichzeitig: aelteste vergessen
        oldest = min(mine, key=lambda n: _presence[n][uid][0])
        _presence[oldest].pop(uid, None)
    if (old is None or bool(old[1]) != editing) and q is not None:
        _put(q, ("presence", {"note_id": nid}))
    state, users = out
    if not any(u["username"] == uid for u in users):
        users.append({"username": uid, "name": request.session.get("name") or uid, "editing": editing})
    for u in users:
        u["me"] = u["username"] == uid
        if u["me"]:
            u["editing"] = editing
    return JSONResponse(dict(state, users=users, ttl=PRESENCE_TTL, live=_S["loop"] is not None), headers={"Cache-Control": "no-store"})


def _here_db(uid, nid, entries):
    with _db() as c:
        return _here(c, uid, nid, entries)


# --- Zuhoerer: Bereiche melden Aenderungen ueber saga.bus -------------------------------------------------
def _share(c, owner, note_id, target_type, target, **_):
    bus.emit_after(c, "shared.changed", {"owner": owner, "note_id": note_id, "target_type": target_type, "target": target})


def _comments(c, note_id, **_):
    bus.emit_after(c, "comments.changed", {"note_id": note_id})


def _seen(c, uid, note_id, **_):
    bus.emit_after(c, "comments.changed", {"note_id": note_id, "only": uid})


def _saved(c, note_id, rev=None, by=None, updated=None, gone=False, origin=None, **_):
    bus.emit_after(c, "note.changed", {"id": note_id, "rev": rev, "by": by, "updated": updated, "gone": gone, "origin": origin})


bus.on("share.set", _share)
bus.on("share.removed", _share)
bus.on("comment.added", _comments)
bus.on("comments.changed", _comments)
bus.on("comment.seen", _seen)
bus.on("note.saved", _saved)
