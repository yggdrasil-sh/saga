"""Saga-Push-Relais (Bugs-DB 2262, docs/PUSH-RELAY.md): nimmt signierte, Ende-zu-Ende-verschluesselte Mitteilungen von
Saga-Servern an und reicht sie mit dem APNs-Schluessel des Betreibers an Apple weiter.

Das Relais sieht nur Steuerdaten (Token, Topic, push-type, priority, expiration, undurchsichtige collapse-id, Loc-Keys/
Flags in aps, Laenge des Chiffrats) - nie Namen oder Texte. Ein Prozess (Zaehler im Speicher), SQLite fuer Server,
Nonces und Tagesstatistik. Aus saga/ wird nur fedsig.py benutzt."""
import asyncio
import contextlib
import html
import json
import re
import time

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from saga import fedsig
from sagarelay import apns as apns_mod
from sagarelay import auth, config, limits, log, store as store_mod, verify

TOKEN_RE = re.compile(r"[0-9a-fA-F]{64,200}")
COLLAPSE_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
LOC_KEYS = ("SAGA_PUSH_TITLE", "SAGA_PUSH_BODY", "SAGA_PUSH_CALL")
VERSION_RE = re.compile(r"[\x20-\x7e]{0,40}")
K_RE = re.compile(r"[A-Za-z0-9_-]{8}")
C_RE = re.compile(r"[A-Za-z0-9_-]{1,4000}")
RATE_FOR = {"voip": ("ring",), "alert": ("alert", "ring"), "background": ("bg",), "liveactivity": ("bg",), "widgets": ("bg",)}
PRIO_FOR = {"background": (5, 1), "widgets": (5, 1)}
APS_KEYS = {"alert", "mutable-content", "content-available", "sound", "interruption-level"}
APS_FOR = {"voip": set(), "background": {"content-available"}}        # sonst APS_KEYS
LEVELS = ("passive", "active", "time-sensitive")
MAX_EXPIRY = 30 * 86400


class Bad(Exception):
    def __init__(self, reason, status=400):
        super().__init__(reason)
        self.reason, self.status = reason, status


def _j(status, body, headers=None):
    return JSONResponse(body, status_code=status, headers=headers)


def _int(v):
    return isinstance(v, int) and not isinstance(v, bool)


# --- Pruefung der Push-Anfrage (docs/PUSH-RELAY.md 3.6) -----------------------------------------------------------
def check_aps(aps, push_type):
    """aps streng: nur Loc-Keys SAGA_PUSH_* und Schalter - Freitext -> Bad("freitext"), sonst Unbekanntes -> Bad("aps")."""
    if not isinstance(aps, dict):
        raise Bad("aps")
    allowed = APS_FOR.get(push_type, APS_KEYS)
    for k, v in aps.items():
        if k not in allowed:
            raise Bad("aps" if k in APS_KEYS else "freitext")
        if k == "alert":
            if not isinstance(v, dict):
                raise Bad("freitext")
            for ak, av in v.items():
                if ak not in ("title-loc-key", "loc-key") or av not in LOC_KEYS:
                    raise Bad("freitext")
        elif k in ("mutable-content", "content-available"):
            if not _int(v) or v != 1:
                raise Bad("aps")
        elif k == "sound":
            if v != "default":
                raise Bad("aps")
        elif k == "interruption-level":
            if v not in LEVELS:
                raise Bad("aps")
    return aps


def check_saga(s):
    if not isinstance(s, dict) or set(s) != {"e2e", "k", "c"}:
        raise Bad("saga")
    if not _int(s["e2e"]) or s["e2e"] != 1 or not isinstance(s["k"], str) or not K_RE.fullmatch(s["k"]) \
            or not isinstance(s["c"], str) or not C_RE.fullmatch(s["c"]):
        raise Bad("saga")
    return s


def check_push(d, cfg, now=None):
    """-> dict mit token, env, team, headers (apns-*), body (Bytes), push_type, rate, prio. Fehler: Bad."""
    now = int(now or time.time())
    if not isinstance(d, dict):
        raise Bad("json")
    tok = d.get("token")
    if not isinstance(tok, str) or not TOKEN_RE.fullmatch(tok):
        raise Bad("token")
    env = d.get("env")
    if env not in ("production", "sandbox") or (env == "sandbox" and not cfg.sandbox):
        raise Bad("env")
    pt = d.get("push_type")
    if not isinstance(pt, str) or pt not in cfg.push_types():
        raise Bad("push_type")
    topic = d.get("topic")
    bundle = cfg.bundle_for(pt, topic) if isinstance(topic, str) else None
    if not bundle:
        raise Bad("topic")
    prio = d.get("priority")
    if not _int(prio) or prio not in PRIO_FOR.get(pt, (10, 5, 1)):
        raise Bad("priority")
    exp = d.get("expiration")
    if not _int(exp) or exp < 0 or exp > now + MAX_EXPIRY:
        raise Bad("expiration")
    cid = d.get("collapse_id")
    if cid is not None and (not isinstance(cid, str) or not COLLAPSE_RE.fullmatch(cid)):
        raise Bad("collapse_id")
    rate = d.get("rate")
    if rate not in RATE_FOR[pt]:
        raise Bad("rate")
    aps = check_aps(d.get("aps"), pt)
    saga = check_saga(d.get("saga"))
    body = json.dumps({"aps": aps, "saga": saga}, separators=(",", ":"), ensure_ascii=True).encode()
    if len(body) > (config.MAX_PAYLOAD_VOIP if pt == "voip" else config.MAX_PAYLOAD):
        raise Bad("size", 413)
    h = {"apns-push-type": pt, "apns-topic": topic, "apns-priority": str(prio), "apns-expiration": str(exp)}
    if cid is not None:
        h["apns-collapse-id"] = cid
    return {"token": tok, "env": env, "team": cfg.topic_team[bundle], "headers": h, "body": body, "push_type": pt,
            "rate": rate, "prio": prio}


# --- Zustand ---------------------------------------------------------------------------------------------------
class Relay:
    def __init__(self, cfg, st):
        self.cfg, self.store = cfg, st
        self.limiter = limits.Limiter(day_loader=lambda sid, d: st.day_count(sid, ("alert",), d))
        self.reg = limits.RegThrottle()
        self.apns = apns_mod.APNs(cfg)
        self._stats, self._seen = {}, {}
        self.gen = asyncio.Semaphore(cfg.apns_concurrency - cfg.ring_slots)
        self.ring = asyncio.Semaphore(cfg.ring_slots)

    def count(self, sid, pt, status):
        k = (store_mod.day(), sid, pt, int(status))
        self._stats[k] = self._stats.get(k, 0) + 1

    def seen(self, sid):
        self._seen[sid] = int(time.time())

    def flush(self):
        st, self._stats = self._stats, {}
        se, self._seen = self._seen, {}
        try:
            self.store.add_stats(st)
            self.store.seen(se)
        except Exception as ex:  # noqa: BLE001 - Statistik darf den Dienst nicht anhalten
            log.emit(ev="flush", err=type(ex).__name__)

    async def slot(self, rate):
        """APNs-Platz: ring nimmt einen allgemeinen, wenn frei, sonst einen reservierten - nie hinter alert/bg."""
        try:
            if rate == "ring":
                if not self.gen.locked():
                    await self.gen.acquire()
                    return self.gen
                await asyncio.wait_for(self.ring.acquire(), self.cfg.slot_wait)
                return self.ring
            await asyncio.wait_for(self.gen.acquire(), self.cfg.slot_wait)
            return self.gen
        except asyncio.TimeoutError:
            return None

    def promote(self, s):
        if s["state"] == "active" and s["stage"] == "neu" and time.time() - s["clean_since"] >= config.PROMOTE_AFTER:
            self.store.update(s["server_id"], stage="aktiv")
            s["stage"] = "aktiv"
        return s

    def view(self, s):
        return {"server_id": s["server_id"], "state": s["state"], "stage": "gesperrt" if s["state"] == "blocked" else s["stage"],
                "limits": self.cfg.limits(s["stage"], s["limits"]), "topics": self.cfg.bundles(), "created": s["created"],
                "host": s["host"], "verified": bool(s["verified"])}


R = {}          # "relay": Relay (im Lebenszyklus angelegt)


def _relay():
    return R["relay"]


async def _read(request):
    cl = request.headers.get("content-length")
    if cl is not None and (not cl.isdigit() or int(cl) > config.MAX_BODY):
        raise Bad("size", 413)
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > config.MAX_BODY:
            raise Bad("size", 413)
    return bytes(data)


def _json(body):
    try:
        return json.loads(body or b"null")
    except (ValueError, RecursionError):
        raise Bad("json")


def _client_ip(request, cfg):
    if cfg.trust_proxy:
        xff = request.headers.get("x-forwarded-for") or ""
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-1]
    return request.client.host if request.client else "-"


def _auth_reason(ex):
    """Grob nach aussen: replay | time | unknown | signature (Feinheiten nur im Protokoll)."""
    e = str(ex)
    if e == "replay":
        return "replay"
    if e == "time":
        return "time"
    if e in ("unknown keyid", "keyid"):
        return "unknown"
    return "signature"


def _ms(t0):
    return int((time.monotonic() - t0) * 1000)


async def _authed(request, ev):
    """Gemeinsam fuer /v1/servers/me* und /v1/push: Koerper lesen, Signatur, Replay. -> (relay, body, server) oder Response."""
    rl = _relay()
    t0 = time.monotonic()
    try:
        body = await _read(request)
    except Bad as ex:
        log.emit(ev=ev, status=ex.status, err=ex.reason, ms=_ms(t0))
        return None, _j(ex.status, {"error": "bad", "reason": ex.reason})
    try:
        _, s = await run_in_threadpool(auth.check, request, body, rl.cfg, rl.store)
    except auth.AuthError as ex:
        log.emit(ev=ev, status=401, err=str(ex), ms=_ms(t0))
        return None, _j(401, {"error": "auth", "reason": _auth_reason(ex)})
    rl.seen(s["server_id"])
    return (rl, body, rl.promote(s), t0), None


# --- Endpunkte ----------------------------------------------------------------------------------------------------
async def info(request):
    rl = _relay()
    c = rl.cfg
    return _j(200, {"v": [1], "push_types": c.push_types(), "topics": c.bundles(), "tiers": c.tiers,
                    "registration": c.registration, "sandbox": c.sandbox, "max_payload": config.MAX_PAYLOAD,
                    "max_payload_voip": config.MAX_PAYLOAD_VOIP, "max_body": config.MAX_BODY})


async def register(request):
    rl = _relay()
    t0 = time.monotonic()
    try:
        body = await _read(request)
        d = _json(body)
        if not isinstance(d, dict) or d.get("v") != 1:
            raise Bad("v")
        pub_s = d.get("pub")
        try:
            pub_raw = fedsig.load_public(pub_s)
        except (fedsig.SigError, ValueError, TypeError):
            raise Bad("pub")
        pub_s = fedsig.b64u(pub_raw)
        try:
            name, port = verify.norm_host(d.get("host"))
        except verify.VerifyError:
            raise Bad("host")
        host = name if port == 443 else f"{name}:{port}"
        ver = d.get("saga_version", "")
        if not isinstance(ver, str) or not VERSION_RE.fullmatch(ver):
            raise Bad("saga_version")
    except Bad as ex:
        log.emit(ev="register", status=ex.status, err=ex.reason, ms=_ms(t0))
        return _j(ex.status, {"error": "bad", "reason": ex.reason})
    try:
        await run_in_threadpool(auth.check, request, body, rl.cfg, rl.store, pub_raw)
    except auth.AuthError as ex:
        log.emit(ev="register", status=401, err=str(ex), ms=_ms(t0))
        return _j(401, {"error": "auth", "reason": _auth_reason(ex)})
    ip = _client_ip(request, rl.cfg)
    old = rl.store.by_pub(pub_s)
    if old and old["state"] == "blocked":
        log.emit(ev="register", server_id=old["server_id"], status=403, state="blocked", ms=_ms(t0))
        return _j(403, {"error": "blocked"})
    need_check = not old or old["state"] == "pending" or old["host"] != host
    why = ""
    if need_check:
        wait = rl.reg.allow(ip, rl.cfg.reg_per_ip_hour)
        if wait:
            log.emit(ev="register", status=429, scope="register", ip_h=log.ip_h(ip), ms=_ms(t0))
            return _j(429, {"error": "rate", "tier": "register", "scope": "register", "retry_after": wait},
                      {"Retry-After": str(wait)})
        ok = False
        if rl.cfg.registration == "approve":
            why = "approve"
        elif rl.store.host_blocked(host):
            why = "host blocked"
        else:
            ok, why = await run_in_threadpool(verify.check, host, pub_s, rl.cfg.verify_test_base)
        state = "active" if ok else "pending"
    if not old:
        try:
            sid = rl.store.create(pub_s, host, ver, state, ok)
        except Exception:  # noqa: BLE001 - gleichzeitige Anmeldung mit demselben Schluessel
            old = rl.store.by_pub(pub_s)
            if not old:
                raise
            sid = None
        if sid:
            s = rl.store.get(sid)
            log.emit(ev="register", server_id=sid, status=201, state=state, err=why or None, ip_h=log.ip_h(ip), ms=_ms(t0))
            out = rl.view(s)
            if why:
                out["verify_error"] = why
            return _j(201, out)
    f = {"saga_version": ver}
    if need_check:
        f.update(host=host, verified=int(ok), state=state)       # neuer Name = neuer Nachweis
        if state == "active" and old["state"] != "active":
            f["clean_since"] = int(time.time())
    rl.store.update(old["server_id"], **f)
    s = rl.store.get(old["server_id"])
    log.emit(ev="register", server_id=s["server_id"], status=200, state=s["state"], err=why or None, ms=_ms(t0))
    out = rl.view(rl.promote(s))
    if why:
        out["verify_error"] = why
    return _j(200, out)


async def me(request):
    r, resp = await _authed(request, "me")
    if resp:
        return resp
    rl, _, s, t0 = r
    if request.method == "DELETE":
        if s["state"] == "blocked":
            log.emit(ev="delete", server_id=s["server_id"], status=403, state="blocked", ms=_ms(t0))
            return _j(403, {"error": "blocked"})
        rl.store.delete(s["server_id"])
        log.emit(ev="delete", server_id=s["server_id"], status=200, ms=_ms(t0))
        return _j(200, {"ok": True})
    log.emit(ev="me", server_id=s["server_id"], status=200, state=s["state"], ms=_ms(t0))
    return _j(200, rl.view(s))


async def rotate(request):
    r, resp = await _authed(request, "rotate")
    if resp:
        return resp
    rl, body, s, t0 = r
    if s["state"] == "blocked":
        log.emit(ev="rotate", server_id=s["server_id"], status=403, state="blocked", ms=_ms(t0))
        return _j(403, {"error": "blocked"})
    try:
        d = _json(body)
        if not isinstance(d, dict):
            raise Bad("json")
        new = d.get("new_pub")
        try:
            new = fedsig.b64u(fedsig.load_public(new))
        except (fedsig.SigError, ValueError, TypeError):
            raise Bad("new_pub")
        if new == s["pub"] or rl.store.by_pub(new):
            raise Bad("key in use")
        if not auth.rotate_proof_ok(s["server_id"], new, d.get("proof")):
            raise Bad("proof")
    except Bad as ex:
        log.emit(ev="rotate", server_id=s["server_id"], status=ex.status, err=ex.reason, ms=_ms(t0))
        return _j(ex.status, {"error": "bad", "reason": ex.reason})
    rl.store.update(s["server_id"], pub=new)
    log.emit(ev="rotate", server_id=s["server_id"], status=200, ms=_ms(t0))
    return _j(200, {"ok": True, "server_id": s["server_id"]})


async def push(request):
    r, resp = await _authed(request, "push")
    if resp:
        return resp
    rl, body, s, t0 = r
    sid = s["server_id"]
    if s["state"] != "active":
        err = "blocked" if s["state"] == "blocked" else "pending"
        rl.count(sid, "-", 403)
        log.emit(ev="push", server_id=sid, status=403, state=s["state"], ms=_ms(t0))
        return _j(403, {"error": err})
    try:
        p = check_push(_json(body), rl.cfg)
    except Bad as ex:
        rl.count(sid, "-", ex.status)
        log.emit(ev="push", server_id=sid, status=ex.status, err=ex.reason, ms=_ms(t0))
        return _j(ex.status, {"error": "bad", "reason": ex.reason})
    lg = {"ev": "push", "server_id": sid, "push_type": p["push_type"], "rate": p["rate"], "prio": p["prio"]}
    hit = rl.limiter.check(sid, p["token"], p["rate"], rl.cfg.limits(s["stage"], s["limits"]))
    if hit:
        scope, after = hit
        rl.count(sid, p["push_type"], 429)
        log.emit(status=429, scope=scope, tok_h=log.tok_h(p["token"]), ms=_ms(t0), **lg)
        return _j(429, {"error": "rate", "tier": s["stage"], "scope": "server" if scope == "day" else scope,
                        "retry_after": after}, {"Retry-After": str(after)})
    sem = await rl.slot(p["rate"])
    if sem is None:
        rl.count(sid, p["push_type"], 429)
        log.emit(status=429, scope="apns", err="busy", tok_h=log.tok_h(p["token"]), ms=_ms(t0), **lg)
        return _j(429, {"error": "rate", "tier": s["stage"], "scope": "apns", "retry_after": 5}, {"Retry-After": "5"})
    try:
        res = await rl.apns.send(p["team"], p["env"], p["token"], p["headers"], p["body"])
    finally:
        sem.release()
    if res.status == 200:
        rl.limiter.delivered(sid, p["rate"])
    rl.count(sid, p["push_type"], res.status)
    log.emit(status=res.status, apns_reason=res.reason or None, ms=_ms(t0),
             tok_h=None if res.status == 200 else log.tok_h(p["token"]), **lg)
    return _j(res.status, res.body, res.headers or None)


PAGE = """<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Saga-Push-Relais</title><style>body{{font:16px/1.5 system-ui,sans-serif;max-width:46rem;margin:2rem auto;padding:0 1rem;color:#222}}
h1{{font-size:1.4rem}}h2{{font-size:1.1rem;margin-top:1.6rem}}code{{background:#f3f3f3;padding:0 .2em}}</style></head><body>
<h1>Saga-Push-Relais</h1>
<p>Dieser Dienst leitet Mitteilungen von Saga-Servern an Apple (APNs) weiter, damit die Saga-App auf iPhone und iPad
Mitteilungen bekommt. Die Inhalte sind zwischen dem Saga-Server und der App Ende-zu-Ende verschl&uuml;sselt &ndash;
das Relais kann sie nicht lesen.</p>
<h2>Datenschutz</h2>
<p>Das Relais verarbeitet nur, was f&uuml;r die Zustellung n&ouml;tig ist: Kennung und Name des Saga-Servers,
Ger&auml;tetoken, Umgebung, App-Kennung (Topic), Art und Priorit&auml;t der Mitteilung, Ablaufzeit, eine undurchsichtige
Zusammenfassungs-Kennung, feste Textschl&uuml;ssel (z.&nbsp;B. <code>SAGA_PUSH_TITLE</code>), die L&auml;nge des
verschl&uuml;sselten Inhalts und die Uhrzeit. Namen, Nachrichtentexte, Anmeldenamen oder Inhalte erreichen das Relais nie.</p>
<p>Protokolliert wird je Anfrage nur Server-Kennung, Art, Stufe, Priorit&auml;t, Ergebnis und Dauer &ndash; nie das
Ger&auml;tetoken oder der Inhalt (bei Fehlern ein t&auml;glich wechselnder, nicht umkehrbarer Kurzwert). Protokolle werden
nach {log_days} Tagen gel&ouml;scht, Tagesz&auml;hler je Server nach 30 Tagen, Schutzwerte gegen Wiederholung nach 10 Minuten.
Ger&auml;tetoken und Inhalte werden nicht gespeichert.</p>
{note}
<h2>English</h2>
<p>This relay forwards end-to-end encrypted notifications from Saga servers to Apple Push Notification service. It never
sees names or message content. Logs contain no device tokens or payloads and are deleted after {log_days} days.</p>
{operator}
<p><small>Protokoll: <code>/v1/info</code> &middot; Quelltext: Saga (AGPL-3.0)</small></p>
</body></html>"""


async def home(request):
    c = _relay().cfg
    note = f"<p>{html.escape(c.privacy_note)}</p>" if c.privacy_note else ""
    op = f"<h2>Betreiber / Operator</h2><p>{html.escape(c.operator)}</p>" if c.operator else ""
    return HTMLResponse(PAGE.format(note=note, operator=op, log_days=c.log_days),
                        headers={"Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff",
                                 "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})


async def _not_found(request, exc):
    return _j(getattr(exc, "status_code", 404), {"error": "not found"})


def create_app(cfg=None, st=None):
    @contextlib.asynccontextmanager
    async def lifespan(app):
        c = cfg or config.load()
        s = st or store_mod.Store()
        rl = R["relay"] = Relay(c, s)

        async def ticker():
            n = 0
            while True:
                await asyncio.sleep(10)
                await run_in_threadpool(rl.flush)
                n += 1
                if n % 60 == 0:
                    await run_in_threadpool(s.cleanup)
        task = asyncio.create_task(ticker())
        log.emit(ev="start", state=c.registration, err=None if c.public_host else "public_host leer: @authority aus Host-Kopf")
        try:
            yield
        finally:
            task.cancel()
            rl.flush()
            await rl.apns.close()
            if st is None:
                s.close()

    routes = [Route("/", home), Route("/v1/info", info), Route("/v1/servers", register, methods=["POST"]),
              Route("/v1/servers/me", me, methods=["GET", "DELETE"]), Route("/v1/servers/me/rotate", rotate, methods=["POST"]),
              Route("/v1/push", push, methods=["POST"])]
    return Starlette(routes=routes, lifespan=lifespan, exception_handlers={404: _not_found, 405: _not_found})
