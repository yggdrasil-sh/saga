"""APNs-Anbindung: HTTP/2-Verbindungen je Umgebung (httpx), Anbieter-Token (ES256) je Team, Erneuerung bei
ExpiredProviderToken, Zuordnung der Apple-Antworten auf die Relais-Antworten (docs/PUSH-RELAY.md 2.1)."""
import base64
import json
import re
import threading
import time

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

GONE = ("BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic")
REASON_RE = re.compile(r"[A-Za-z]{1,40}")
JWT_MAX_AGE = 40 * 60            # Apple: hoechstens alle 20 min neu, hoechstens 60 min alt


def _b64u(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def jwt_es256(key, key_id, team_id, now=None):
    now = int(now or time.time())
    seg = _b64u(json.dumps({"alg": "ES256", "kid": key_id}, separators=(",", ":")).encode()) + "." + \
        _b64u(json.dumps({"iss": team_id, "iat": now}, separators=(",", ":")).encode())
    r, s = decode_dss_signature(key.sign(seg.encode(), ec.ECDSA(hashes.SHA256())))
    return seg + "." + _b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


class Tokens:
    def __init__(self):
        self._lock = threading.Lock()
        self._c = {}

    def get(self, team, renew=False):
        k = team["team_id"] + "|" + team["key_id"]
        with self._lock:
            hit = self._c.get(k)
            if renew or not hit or time.time() - hit[0] > JWT_MAX_AGE:
                now = int(time.time())
                hit = self._c[k] = (now, jwt_es256(team["key"], team["key_id"], team["team_id"], now))
            return hit[1]


class Result:
    """status/body = Antwort des Relais an den Saga-Server; reason = Apple-Grund (fuers Protokoll)."""
    __slots__ = ("status", "body", "reason", "headers")

    def __init__(self, status, body, reason="", headers=None):
        self.status, self.body, self.reason, self.headers = status, body, reason, headers or {}


def _reason(r):
    try:
        v = str((r.json() or {}).get("reason") or "")
    except (ValueError, AttributeError):
        return ""
    return v if REASON_RE.fullmatch(v) else ""


def map_response(status, reason):
    """Apple-Antwort -> Result (ohne ExpiredProviderToken, das behandelt send)."""
    if status == 200:
        return Result(200, {"ok": True}, "")
    if status == 410 or reason in GONE:
        return Result(410, {"error": "gone", "reason": reason or "Unregistered"}, reason)
    if status == 429:
        # Apple "TooManyRequests" gilt einem einzelnen Geraetetoken (2262-1d, Bugs-DB 2412): nur diesen Auftrag anhalten,
        # nicht alle Mitteilungen und Anrufe des Servers
        return Result(429, {"error": "rate", "tier": "apns", "scope": "token", "retry_after": 60}, reason,
                      {"Retry-After": "60"})
    if status == 413:
        return Result(413, {"error": "bad", "reason": reason or "PayloadTooLarge"}, reason)
    if status == 400 or status == 405 or status == 404:
        return Result(400, {"error": "bad", "reason": reason or f"apns {status}"}, reason)
    # 403 (Schluessel/Team des Relais), 5xx: Relais/Apple gestoert - der Server haelt die Auftraege zurueck
    return Result(502, {"error": "upstream", "reason": reason or f"apns {status}"}, reason)


class APNs:
    def __init__(self, cfg):
        self.cfg = cfg
        self.tokens = Tokens()
        self._clients = {}

    def _client(self, base):
        cl = self._clients.get(base)
        if cl is None:
            t = self.cfg.apns_timeout
            cl = self._clients[base] = httpx.AsyncClient(
                http2=True, follow_redirects=False, trust_env=False,
                timeout=httpx.Timeout(t, connect=min(t, 5.0)),
                # HTTP/2 buendelt viele Anfragen auf wenige Verbindungen; die Obergrenze zaehlt fuer HTTP/1.1 (Tests)
                limits=httpx.Limits(max_connections=self.cfg.apns_concurrency, max_keepalive_connections=8))
        return cl

    async def close(self):
        for cl in list(self._clients.values()):
            await cl.aclose()
        self._clients.clear()

    async def send(self, team, env, token, headers, body):
        """headers: apns-*-Koepfe (unveraendert vom Server), body: Bytes {"aps":...,"saga":...}."""
        base = self.cfg.apns_base(env)
        url = f"{base}/3/device/{token}"
        for attempt in (0, 1):
            h = dict(headers)
            h["authorization"] = "bearer " + self.tokens.get(team, renew=bool(attempt))
            h["content-type"] = "application/json"
            try:
                r = await self._client(base).post(url, content=body, headers=h)
            except httpx.TimeoutException:
                return Result(502, {"error": "upstream", "reason": "timeout"}, "timeout")
            except (httpx.HTTPError, OSError) as ex:
                return Result(502, {"error": "upstream", "reason": "connect"}, type(ex).__name__[:40])
            reason = _reason(r) if r.status_code != 200 else ""
            if r.status_code == 403 and reason == "ExpiredProviderToken" and not attempt:
                continue
            return map_response(r.status_code, reason)
        return Result(502, {"error": "upstream", "reason": "ExpiredProviderToken"}, "ExpiredProviderToken")
