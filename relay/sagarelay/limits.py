"""Ratenstufen (docs/PUSH-RELAY.md 2.4): Token-Bucket je Server und je Token, getrennt nach ring/alert/bg.

Alles im Speicher (ein Prozess). Token werden nur als HMAC mit einem Prozess-Schluessel gehalten. ring hat eigene Toepfe -
volle alert/bg-Toepfe bremsen Anrufe nie. Tagesdeckel (alert.per_day) zaehlt Zustellungen je Server und UTC-Tag; beim ersten
Zugriff am Tag wird der Stand aus daily_stats uebernommen (Neustart setzt den Deckel nicht zurueck)."""
import hashlib
import hmac
import math
import secrets
import threading
import time

_KEY = secrets.token_bytes(32)
IDLE = 900                       # Toepfe, die so lange unbenutzt sind, fallen weg
CLOCK = time.monotonic           # Tests frieren die Uhr ein (genaue Zaehlung ohne Nachfuellen)


def _h(token):
    return hmac.new(_KEY, str(token).encode(), hashlib.sha256).digest()[:16]


class Bucket:
    __slots__ = ("cap", "rate", "level", "ts")

    def __init__(self, per_min, now):
        self.cap = float(per_min)
        self.rate = float(per_min) / 60.0
        self.level = float(per_min)
        self.ts = now

    def wait(self, now):
        """Sekunden bis ein Platz frei ist (0 = jetzt)."""
        self.level = min(self.cap, self.level + (now - self.ts) * self.rate)
        self.ts = now
        if self.level >= 1:
            return 0.0
        if self.rate <= 0:
            return 3600.0
        return (1 - self.level) / self.rate

    def take(self):
        self.level -= 1


class Limiter:
    def __init__(self, day_loader=None):
        self._lock = threading.Lock()
        self._b = {}                     # (scope, id, rate) -> Bucket
        self._day = {}                   # server_id -> [tag, zahl]
        self._day_loader = day_loader    # (server_id, tag) -> Zustellungen heute (aus der DB)
        self._last_prune = CLOCK()

    def _bucket(self, key, per_min, now):
        b = self._b.get(key)
        if b is None or b.cap != float(per_min):
            b = self._b[key] = Bucket(per_min, now)
        return b

    def check(self, server_id, token, rate, limits, now=None, today=None):
        """-> None (frei, verbraucht) oder (scope, retry_after). limits: wirksame Stufe {rate: {per_min, per_token_min, per_day}}."""
        now = now if now is not None else CLOCK()
        lim = limits.get(rate) or {}
        with self._lock:
            self._prune(now)
            checks = []
            if "per_min" in lim:
                checks.append(("server", self._bucket(("s", server_id, rate), lim["per_min"], now)))
            if "per_token_min" in lim:
                checks.append(("token", self._bucket(("t", _h(token), rate), lim["per_token_min"], now)))
            for scope, b in checks:
                w = b.wait(now)
                if w > 0:
                    return scope, _after(w)
            if lim.get("per_day") is not None:
                d = self._day_count(server_id, today)
                if d[1] >= lim["per_day"]:
                    t = time.time()
                    return "day", _after(86400 - (t % 86400))
            for _, b in checks:
                b.take()
            return None

    def delivered(self, server_id, rate, today=None):
        """Zustellung zaehlen (nur alert hat einen Tagesdeckel). Nach einem Neustart kommt der Stand aus daily_stats
        (push_type alert, also auch Anrufe ohne VoIP-Abo mit rate ring - dann eher etwas zu streng als zu locker)."""
        if rate != "alert":
            return
        with self._lock:
            self._day_count(server_id, today)[1] += 1

    def _day_count(self, server_id, today):
        today = today or time.strftime("%Y-%m-%d", time.gmtime())
        d = self._day.get(server_id)
        if not d or d[0] != today:
            n = 0
            if self._day_loader:
                try:
                    n = int(self._day_loader(server_id, today))
                except Exception:  # noqa: BLE001 - Statistik darf das Zustellen nicht verhindern
                    n = 0
            d = self._day[server_id] = [today, n]
        return d

    def _prune(self, now):
        if now - self._last_prune < 60:
            return
        self._last_prune = now
        for k in [k for k, b in self._b.items() if now - b.ts > IDLE]:
            del self._b[k]

    def reset(self):
        with self._lock:
            self._b.clear()
            self._day.clear()


def _after(sec):
    return int(min(max(math.ceil(sec), 1), 3600))


class RegThrottle:
    """Anmelde-Bremse: hoechstens n neue Anmeldungen je IP und Stunde (IP nur als HMAC im Speicher)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._hits = {}

    def allow(self, ip, n, now=None):
        now = now if now is not None else CLOCK()
        k = _h("ip|" + str(ip))
        with self._lock:
            for kk in [kk for kk, v in self._hits.items() if not v or now - v[-1] > 3600]:
                del self._hits[kk]
            h = [t for t in self._hits.get(k, []) if now - t < 3600]
            if len(h) >= n:
                self._hits[k] = h
                return _after(3600 - (now - h[0]))
            h.append(now)
            self._hits[k] = h
            return 0

    def reset(self):
        with self._lock:
            self._hits.clear()
