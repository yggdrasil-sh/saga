"""Wetter fuer das Widget "weather" (2.5, Paket 4) - Daten von Open-Meteo (https://open-meteo.com, CC BY 4.0).

- Nur der SERVER ruft ab (Browser/Apps kontaktieren Open-Meteo nie). Admin-Schalter weather.enabled (Standard aus);
  base_url/geo_url leer = oeffentliche Open-Meteo-Adressen, sonst eigene Instanz (interne Adressen nur mit Freigabe unter
  "Freigegebene interne Hosts" bzw. allow_private_hosts, http nur fuer freigegebene Hosts).
- SSRF-Schutz: saga.netguard (DNS einmal aufloesen, Ziel-IP pruefen, mit genau dieser IP verbinden), keine Weiterleitungen,
  Zeitlimit 5 s, Antwort hoechstens 64 KB, SAGA_RESTORE_TEST=1 -> nichts geht raus. Parameter fest zusammengebaut,
  Nutzereingaben nur als Zahlen (lat/lon) bzw. als Suchtext in der Query (nie als Teil von Host/Pfad).
- Zwischenspeicher im Speicher je (Adresse, lat, lon, Einheiten): frisch 30 min, bei Fehlern bis 6 h alten Stand liefern
  (stale: true); gleichzeitige Anfragen auf denselben Schluessel holen nur einmal; Bremse je Tag (WEATHER_DAILY_MAX,
  Standard 2000 Abrufe - Open-Meteo frei: nicht-kommerziell, bis 10 000 am Tag).
- Ergebnis (W.data des Widgets): {"now": {"t", "code", "wind"}, "days": [{"d", "min", "max", "code", "rain"}],
  "units": "metric"|"imperial", "stale": bool, "place": str, "as_of": ISO-Zeit} - Wettercodes WMO (docs/APP-API.md)."""
import json
import os
import re
import threading
import time
from urllib.parse import urlencode, urlsplit

import httpcore
import httpx

import auth
from saga import netguard
from saga.i18n import t

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
TIMEOUT, MAX_BYTES = 5.0, 64 * 1024
FRESH_S = int(os.environ.get("SAGA_WEATHER_FRESH_S", "1800") or 1800)    # nur Tests verkuerzen das
STALE_S = 6 * 3600
DAILY_MAX = int(os.environ.get("WEATHER_DAILY_MAX", "2000") or 2000)
DAYS_MAX = 7
GEO_MAX = 8
CACHE_MAX = 2000
HOST_RE = re.compile(r"[a-z0-9.-]{1,253}(:[0-9]{1,5})?")


class WeatherError(Exception):
    """Abruf nicht moeglich: code = disabled | unavailable."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def cfg():
    """Admin-Einstellungen (Standard: aus)."""
    w = auth.settings().get("weather") or {}
    return {"enabled": w.get("enabled") is True, "base_url": str(w.get("base_url") or "").strip(),
            "geo_url": str(w.get("geo_url") or "").strip(), "api_key": str(w.get("api_key") or "")}


def enabled():
    return cfg()["enabled"]


def _admin_hosts():
    s = auth.settings()
    return {h.strip().lower() for h in re.split(r"[,\s]+", s.get("allowed_hosts") or "") if h.strip()}


def check_url(u):
    """Adresse fuer eine eigene Open-Meteo-Instanz pruefen -> (normalisiert, fehler). Leer = Standard."""
    u = str(u or "").strip()
    if not u:
        return "", None
    if len(u) > 300:
        return None, t("Adresse zu lang")
    try:
        p = urlsplit(u)
        port = p.port
    except ValueError:
        return None, t("Adresse ungültig")
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password or p.query or p.fragment:
        return None, t("Adresse ungültig (z. B. https://wetter.example.org/v1/forecast)")
    free = p.netloc.lower() in _admin_hosts() or (p.hostname or "").lower() in _admin_hosts()
    if p.scheme == "http" and not free:
        return None, t("Nur https-Adressen erlaubt (http nur für freigegebene interne Hosts)")
    if port not in (None, 443, 80) and not free:
        return None, t("Andere Ports nur für freigegebene interne Hosts")
    return f"{p.scheme}://{p.netloc}{p.path}", None


def _ip_ok(ip, host, port):
    """Beim Verbinden: oeffentliche IP, sonst nur freigegebene Hosts (Admin) bzw. allow_private_hosts."""
    host = (host or "").lower()
    allowed = _admin_hosts()
    return netguard.is_public(ip) or bool(auth.settings().get("allow_private_hosts")) or host in allowed or f"{host}:{port}" in allowed


def _check_req(req):
    """Jede Anfrage: nur http(s), keine Zugangsdaten, http/fremde Ports nur fuer freigegebene Hosts."""
    u = req.url
    free = f"{u.host}:{u.port}".lower() in _admin_hosts() or (u.host or "").lower() in _admin_hosts()
    if u.scheme not in ("http", "https") or u.userinfo or (u.scheme == "http" and not free) or (u.port not in (None, 443, 80) and not free):
        raise httpx.UnsupportedProtocol("nicht erlaubt")


def _get(url, params):
    """GET -> JSON (dict). Fehler -> WeatherError('unavailable')."""
    if netguard.RESTORE_TEST:
        raise WeatherError("unavailable")
    try:
        with netguard.guarded_client(_ip_ok, timeout=TIMEOUT, follow_redirects=False,
                                     event_hooks={"request": [_check_req]}) as cl:
            with cl.stream("GET", url + "?" + urlencode(params), headers={"User-Agent": "Saga-Weather/1", "Accept": "application/json"}) as r:
                if r.status_code != 200:
                    raise WeatherError("unavailable")
                data = b""
                for chunk in r.iter_bytes():
                    data += chunk
                    if len(data) > MAX_BYTES:
                        raise WeatherError("unavailable")
    except WeatherError:
        raise
    except (httpx.HTTPError, httpcore.ConnectError, OSError, ValueError):
        raise WeatherError("unavailable")
    try:
        d = json.loads(data)
    except ValueError:
        raise WeatherError("unavailable")
    if not isinstance(d, dict):
        raise WeatherError("unavailable")
    return d


# --- Bremse je Tag (ganze Instanz) --------------------------------------------------------------------------------------
_budget = {"day": "", "n": 0}
_blk = threading.Lock()


def _spend():
    day = time.strftime("%Y-%m-%d", time.gmtime())
    with _blk:
        if _budget["day"] != day:
            _budget["day"], _budget["n"] = day, 0
        if _budget["n"] >= DAILY_MAX:
            return False
        _budget["n"] += 1
        return True


# --- Zwischenspeicher -----------------------------------------------------------------------------------------------
_cache = {}            # key -> (zeit, daten)
_locks = {}
_clk = threading.Lock()


def _lock_for(key):
    with _clk:
        lk = _locks.get(key)
        if lk is None:
            if len(_locks) > CACHE_MAX:
                _locks.clear()
            lk = _locks[key] = threading.Lock()
        return lk


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) < 1e6 else None


def _int(v):
    x = _num(v)
    return int(x) if x is not None else None


def parse(d, units, ndays):
    """Open-Meteo-Antwort -> W.data (ohne place/stale/as_of). Fehlt etwas Wesentliches: WeatherError('unavailable')."""
    cur = d.get("current") if isinstance(d.get("current"), dict) else {}
    daily = d.get("daily") if isinstance(d.get("daily"), dict) else {}
    tt = daily.get("time") if isinstance(daily.get("time"), list) else []
    col = lambda k: daily.get(k) if isinstance(daily.get(k), list) else []      # noqa: E731
    mx, mn, code, rain = col("temperature_2m_max"), col("temperature_2m_min"), col("weather_code"), col("precipitation_probability_max")
    days = []
    for i, day in enumerate(tt[:ndays]):
        if not (isinstance(day, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day)):
            continue
        g = lambda a: a[i] if i < len(a) else None    # noqa: E731
        days.append({"d": day, "min": _num(g(mn)), "max": _num(g(mx)), "code": _int(g(code)), "rain": _int(g(rain))})
    now = {"t": _num(cur.get("temperature_2m")), "code": _int(cur.get("weather_code")), "wind": _num(cur.get("wind_speed_10m"))}
    if now["t"] is None and not days:
        raise WeatherError("unavailable")
    return {"now": now, "days": days, "units": units}


def forecast(lat, lon, units="metric", days=5):
    """Vorhersage (aus dem Zwischenspeicher oder frisch) -> dict wie W.data (ohne place). WeatherError bei Problemen."""
    c = cfg()
    if not c["enabled"]:
        raise WeatherError("disabled")
    base, err = check_url(c["base_url"])
    if err or base is None:
        raise WeatherError("unavailable")
    base = base or FORECAST_URL
    ndays = max(1, min(DAYS_MAX + 1, int(days) + 1))
    key = (base, round(lat, 2), round(lon, 2), units)
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < FRESH_S and len(hit[1]["days"]) >= ndays:
        return _view(hit, ndays, False)
    with _lock_for(key):                                     # gleichzeitige Anfragen: nur einer holt
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < FRESH_S and len(hit[1]["days"]) >= ndays:
            return _view(hit, ndays, False)
        try:
            if not _spend():
                raise WeatherError("unavailable")
            params = {"latitude": f"{key[1]:.2f}", "longitude": f"{key[2]:.2f}",
                      "current": "temperature_2m,weather_code,wind_speed_10m",
                      "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                      "timezone": "auto", "forecast_days": str(DAYS_MAX + 1)}
            if units == "imperial":
                params.update(temperature_unit="fahrenheit", wind_speed_unit="mph", precipitation_unit="inch")
            if c["api_key"]:
                params["apikey"] = c["api_key"]
            data = parse(_get(base, params), units, DAYS_MAX + 1)
        except WeatherError:
            if hit and time.time() - hit[0] < STALE_S:
                return _view(hit, ndays, True)
            raise
        ent = (time.time(), data)
        with _clk:
            if len(_cache) >= CACHE_MAX:
                for k, _ in sorted(_cache.items(), key=lambda kv: kv[1][0])[:CACHE_MAX // 4]:
                    _cache.pop(k, None)
            _cache[key] = ent
        return _view(ent, ndays, False)


def cached(lat, lon, units="metric", days=5):
    """Nur aus dem Zwischenspeicher (Veroeffentlichen: Besucher loesen nie einen Abruf aus) -> dict oder None."""
    c = cfg()
    if not c["enabled"]:
        return None
    base, err = check_url(c["base_url"])
    if err or base is None:
        return None
    hit = _cache.get((base or FORECAST_URL, round(lat, 2), round(lon, 2), units))
    if not hit or time.time() - hit[0] >= STALE_S:
        return None
    return _view(hit, max(1, min(DAYS_MAX + 1, int(days) + 1)), time.time() - hit[0] >= FRESH_S)


def _view(ent, ndays, stale):
    t0, d = ent
    return {"now": dict(d["now"]), "days": [dict(x) for x in d["days"][:ndays]], "units": d["units"], "stale": stale,
            "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0))}


def geocode(q, lang="de"):
    """Ortssuche -> [{name, admin, country, lat, lon}] (hoechstens 8). WeatherError bei Problemen."""
    c = cfg()
    if not c["enabled"]:
        raise WeatherError("disabled")
    base, err = check_url(c["geo_url"])
    if err or base is None:
        raise WeatherError("unavailable")
    if not _spend():
        raise WeatherError("unavailable")
    params = {"name": q[:80], "count": str(GEO_MAX), "language": lang if re.fullmatch(r"[a-z]{2}", lang or "") else "en", "format": "json"}
    if c["api_key"]:
        params["apikey"] = c["api_key"]
    d = _get(base or GEO_URL, params)
    out = []
    for r in (d.get("results") if isinstance(d.get("results"), list) else [])[:GEO_MAX]:
        if not isinstance(r, dict):
            continue
        lat, lon = _num(r.get("latitude")), _num(r.get("longitude"))
        if lat is None or lon is None or not -90 <= lat <= 90 or not -180 <= lon <= 180:
            continue
        txt = lambda k: re.sub(r"[\x00-\x1f<>]", "", str(r.get(k) or ""))[:80]     # noqa: E731
        out.append({"name": txt("name"), "admin": txt("admin1"), "country": txt("country"), "lat": round(lat, 2), "lon": round(lon, 2)})
    return out


def reset_cache():
    """Nur fuer Tests/Admin: Zwischenspeicher und Bremse leeren."""
    with _clk:
        _cache.clear()
    with _blk:
        _budget["day"], _budget["n"] = "", 0
