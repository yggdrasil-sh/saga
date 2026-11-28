"""Diktat ueber den Server (Saga 2.6, Bugs-DB 2274 K-F, Konzept K16/K17, FEATURES aistt1) - Vertrag docs/APP-API-KI.md
Abschnitt 15, Vektoren tests/vectors/stt_contract.json, Tests tests/kollegin_diktat.py (+ tests/fakewhisper.py).

Der Server reicht eine kurze Aufnahme (Ogg/Opus oder WAV, hoechstens 60 s / 2 MiB) an einen Whisper-kompatiblen Dienst
weiter (POST {url}/v1/audio/transcriptions) und gibt nur den Text zurueck. Der Ton bleibt im Speicher (roher Koerper per
request.stream() mit Deckel - nie request.form(), das Starlette ab 1 MB in eine Temp-Datei schreibt; keine Temp-Datei,
kein Cache), landet nie im Log und nie in ai_history/ai_turn/job - der Text wird erst zur Bitte, wenn die Person ihn
sendet. Logs nennen nur Zahlen, Ausnahmen nur ihren Typnamen.

Ziel-Adresse (Admin, Einstellung ai_stt): beim Speichern aufgeloest und festgehalten (pinned). Ohne "Cloud erlauben"
nur Adressen im eigenen Netz (ai_saga.home_ips) - verbunden wird nur mit festgehaltenen, nicht oeffentlichen IPs
(netguard.GuardBackend loest je Verbindung einmal auf und verbindet genau die gepruefte IP: Schutz vor DNS-Rebinding).
Mit "Cloud erlauben" auch oeffentliche Adressen, aber nur ueber https. Immer gesperrt: Metadaten-Adressen (METADATA).
Loest ein Heimnetz-Name spaeter oeffentlich auf: ai_saga.mark_left_home(..., key="ai_stt") -> Hinweis "im Internet",
ohne Cloud {on: false}, bis der Admin neu speichert. Keine Weiterleitungen, keine Zugangsdaten in der Adresse,
SAGA_RESTORE_TEST sperrt ueber netguard.

Grenzen: 1 Diktat gleichzeitig je Person (409 busy), 2 serverweit (503 full), 30 je Stunde und 120 je Tag (gleitend, im
Speicher dieses Prozesses; 429 rate). Gemessen (nicht begrenzt) im Pool stt von ai_units: Einheiten = Zeichen/4,
real_in = Sekunden Ton."""
import asyncio
import concurrent.futures
import json
import math
import re
import secrets
import threading
import time
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import auth
from db import connect as _db
from saga import netguard, testclock
from saga.core import _api_guard, _bad
from saga.i18n import t

router = APIRouter()
LANGS = ("de", "en", "es", "ru", "th")              # Sprachen des Diktats (= App-Sprachen)
FORMATS = ("ogg", "wav")                            # Ogg/Opus, WAV (PCM 16 bit mono)
CTYPES = {"audio/ogg": "ogg", "audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav"}
MAX_S = 60                                          # Sekunden je Aufnahme (Pruefung mit 0,5 s Spiel)
MAX_SLACK = 0.5
MAX_BYTES = 2 * 1024 * 1024                         # Koerper hoechstens 2 MiB
BODY_LIMIT = MAX_BYTES + 4096                       # vorgeschaltete Grenze (app.py BodyLimit)
TEXT_MAX = 1000                                     # Zeichen Text (sonst gekuerzt, cut)
RESP_MAX = 64 * 1024                                # Antwort des Dienstes hoechstens 64 KB
RATE_H, RATE_D = 30, 120                            # Diktate je Person und Stunde / Tag
PER_USER, SERVER_MAX = 1, 2                         # gleichzeitig je Person / serverweit
FULL_RETRY = 5                                      # Retry-After bei 503 full (s)
STALE = 180                                         # s: ein belegter Platz gilt danach als verwaist
READ_S = 20.0                                       # s: Gesamtfrist fuers Lesen des Koerpers (sonst 408 slow), Grundwert
READ_RATE = 32768                                   # Byte/s: mit Content-Length waechst die Frist um Laenge/READ_RATE ...
READ_MAX = 60.0                                     # ... bis hoechstens READ_MAX s (1,9 MB WAV -> ~58 s); chunked: READ_S


def read_limit(length):
    """Gesamtfrist fuers Lesen -> s: mit Content-Length min(READ_MAX, READ_S + Laenge/READ_RATE), sonst READ_S."""
    return min(READ_MAX, READ_S + length / READ_RATE) if length else READ_S
TIMEOUT_DEF, TIMEOUT_MIN, TIMEOUT_MAX = 30, 5, 60
INPUTS = ("text", "dictation")                      # POST /api/ai/jobs input
PATH = "/v1/audio/transcriptions"
# Metadaten-Dienste der Cloud-Anbieter - nie, auch nicht festgehalten (169.254.0.0/16 gilt sonst als "im Heimnetz")
METADATA = frozenset(("169.254.169.254", "169.254.170.2", "fd00:ec2::254", "100.100.100.200"))
NO_STORE = {"Cache-Control": "no-store"}


def where_to(h):
    """Ziel fuer den Satz "... uebers Internet an {where} ..." - ohne "(im Internet)" (steht schon im Satz)."""
    if not h:
        return ""
    if h.get("provider") and h.get("country_name"):
        return t("{host} ({provider}, {country})", host=h["host"], provider=h["provider"], country=h["country_name"])
    if h.get("provider"):
        return t("{host} ({provider})", host=h["host"], provider=h["provider"])
    return h["host"]


def notice_text(h, persona=None, lang=None):
    """Hinweis am Knopf "Diktieren" (vor dem ersten Start und waehrend der Aufnahme) -> str. h = Hinweis wie
    ai_saga.endpoint_hint (host, net, provider?, country_name?, note?, label). Sie-Fassung ueber die Bausteine stt.home /
    stt.internet (nur de/es/ru); der Zusatzsatz des Admins (note) haengt hinten an."""
    if not h:
        return ""
    home = h.get("net") == "home"
    where = h.get("label") if home else where_to(h)
    du = (t("Deine Aufnahme geht an {where} und wird dort zu Text. Saga speichert sie nicht.", where=where) if home else
          t("Deine Aufnahme geht übers Internet an {where} und wird dort zu Text. Saga speichert sie nicht.", where=where))
    out = du
    try:
        from saga import ai_say, i18n
        out = ai_say.formal("stt.home" if home else "stt.internet", lang or i18n.get_lang(), persona, where=where) or du
    except Exception:  # noqa: BLE001 - Baustein kaputt: Systemtext (du)
        out = du
    return out + (" " + h["note"] if h.get("note") else "")


def origin_text():
    return t("Diktiert")


def warn_text(persona=None, lang=None):
    """Warnung auf der Plan-Karte (Herkunft dictation, warn) - Sie-Fassung ueber den Baustein stt.warn (de/es/ru)."""
    du = t("Im diktierten Text stehen Anweisungen an die KI – vielleicht aus Radio oder Fernseher. Prüf, ob das deine Bitte ist.")
    try:
        from saga import ai_say, i18n
        return ai_say.formal("stt.warn", lang or i18n.get_lang(), persona) or du
    except Exception:  # noqa: BLE001 - Baustein kaputt: Systemtext (du)
        return du


def message(code, **kw):
    """Fehlertext zu einem code der Fehlertabelle (APP-API-KI 15.1) in der Sprache der Anfrage."""
    if code == "key":
        return t("Diktat gibt es nur in Saga selbst – nicht über API-Schlüssel.")
    if code == "ai":
        return t("Der KI-Assistent ist für dich nicht eingeschaltet.")
    if code == "off":
        return t("Diktat ist auf diesem Server nicht eingerichtet.")
    if code == "device_only":
        return t("Du hast eingestellt, dass Diktat nur auf dem Gerät läuft.")
    if code == "lang":
        return t("Diese Sprache geht beim Diktat nicht.")
    if code == "format":
        return t("Dieses Tonformat geht nicht – nur Ogg/Opus oder WAV (PCM, 16 Bit, mono).")
    if code == "format_service":
        return t("Dieses Tonformat nimmt der Diktat-Dienst nicht an.")
    if code == "empty":
        return t("Die Aufnahme ist leer.")
    if code == "too_long":
        return t("Die Aufnahme ist zu lang (höchstens {n} Sekunden).", n=kw.get("n", MAX_S))
    if code == "too_big":
        return t("Die Aufnahme ist zu groß.")
    if code == "busy":
        return t("Es läuft schon ein Diktat.")
    if code == "slow":
        return t("Die Aufnahme kam zu langsam an – versuch es noch mal.")
    if code == "full":
        return t("Gerade diktieren zu viele gleichzeitig – versuch es gleich noch mal.")
    if code == "rate":
        return t("Für jetzt sind es genug Diktate – versuch es später noch mal.")
    if code == "blocked":
        return t("Der Diktat-Dienst ist gerade nicht erlaubt – es wurde nichts gesendet. Sag deinem Admin Bescheid.")
    if code == "timeout":
        return t("Der Diktat-Dienst hat zu lange gebraucht.")
    if code == "input":
        return t("Unbekannte Eingabeart.")
    return t("Der Diktat-Dienst hat einen Fehler gemeldet.")       # upstream


# --- Einstellung (Admin › KI-Assistent › Diktat) ---------------------------------------------------------------------
def cfg():
    return dict(auth.DEFAULTS["ai_stt"], **(auth.settings().get("ai_stt") or {}))


def configured(c=None):
    c = c if c is not None else cfg()
    return bool(c.get("enabled") and c.get("url") and c.get("model"))


def formats_of(c):
    return [f for f in FORMATS if f in (c.get("formats") or ())]


def norm_url(url):
    """Adresse vereinheitlichen -> (basis, fehler). .../v1/audio/transcriptions bzw. .../v1 faellt weg (Saga haengt
    /v1/audio/transcriptions selbst an); nur http(s), ohne Zugangsdaten, Query und Fragment."""
    from saga import ai
    u = str(url or "").strip().rstrip("/")
    for suf in (PATH, "/audio/transcriptions", "/v1"):
        if u.endswith(suf):
            u = u[: -len(suf)]
            break
    return ai.norm_url(u, "stt")


def _ipn(ip):
    from saga import ai
    return ai._ipn(ip)


def _view(c):
    """Einstellung als 'Endpunkt' fuer ai_saga.endpoint_hint (Host, festgehaltene IPs, left_home, Zusatzsatz)."""
    return {"url": c.get("url") or "", "pinned": list(c.get("pinned") or []), "left_home": c.get("left_home"),
            "endpoint_note": c.get("note") or ""}


def hint(c=None):
    """-> {host, net, provider?, country?, country_name?, note?, label} oder None."""
    from saga import ai_saga
    c = c if c is not None else cfg()
    return ai_saga.endpoint_hint(_view(c)) if c.get("url") else None


def blocked_now(c):
    """Eingerichtet, aber gesperrt: der Name loest jetzt oeffentlich auf (left_home) und Cloud ist aus."""
    from saga import ai_saga
    return bool(ai_saga.left_home(c)) and not c.get("cloud")


def ip_ok(c):
    """ip_ok(ip, host, port) fuer netguard.GuardBackend. Nur der Host der Adresse; Metadaten-Adressen nie.
    cloud aus: nur festgehaltene, nicht oeffentliche IPs im eigenen Netz. cloud an: http nur wie ohne Cloud; https
    oeffentlich oder festgehalten. Lag der Name beim Speichern im Heimnetz und loest jetzt oeffentlich auf: Markierung
    left_home (einmal; danach mit Cloud wie "im Internet", ohne Cloud gesperrt) und diese Verbindung verweigert."""
    from saga import ai_saga
    from saga.netguard import is_public
    pu = urlparse(c.get("url") or "")
    host, plain, cloud = (pu.hostname or "").lower(), pu.scheme == "http", bool(c.get("cloud"))
    pinned = {_ipn(x) for x in c.get("pinned") or []}
    home = ai_saga.home_ips(pinned) and not ai_saga.left_home(c)

    def ok(ip, h, _port):
        n = _ipn(ip) or str(ip)
        if not host or str(h or "").lower().strip("[]") != host.strip("[]") or n in METADATA:
            return False
        inside = ai_saga.home_ips([n])
        if home and not inside:
            ai_saga.mark_left_home(c, n, key="ai_stt")
            return False
        if plain or not cloud:
            return inside and n in pinned and not is_public(ip)
        return is_public(ip) or n in pinned
    return ok


def _hook(c):
    """event_hook: nur http(s), ohne Zugangsdaten, nur der eingetragene Host (jeder Port) - auch bei 3xx nie weiter."""
    host = (urlparse(c.get("url") or "").hostname or "").lower()

    def check(req):
        u = req.url
        if u.scheme not in ("http", "https") or u.userinfo or (u.host or "").lower() != host:
            raise httpx.UnsupportedProtocol("nicht erlaubt")
    return check


def _verify():
    from saga import memory
    return memory._verify()


# --- Formate ---------------------------------------------------------------------------------------------------------
class Bad(Exception):
    def __init__(self, code, **kw):
        super().__init__(code)
        self.code, self.kw = code, kw


def _u16(b, i):
    return int.from_bytes(b[i:i + 2], "little")


def _u32(b, i):
    return int.from_bytes(b[i:i + 4], "little")


def wav_seconds(b):
    """RIFF/WAVE, PCM (Format 1), 16 bit, mono, 8-48 kHz -> Dauer in s. Gerechnet wird mit allen Bytes ab dem Block
    data (nicht der angegebenen Groesse) - ein Decoder liest ggf. weiter. Bad(format) sonst."""
    if len(b) < 12 or b[:4] != b"RIFF" or b[8:12] != b"WAVE":
        raise Bad("format")
    i, fmt = 12, None
    for _ in range(64):
        if i + 8 > len(b):
            break
        cid, size = bytes(b[i:i + 4]), _u32(b, i + 4)
        body = i + 8
        if cid == b"fmt ":
            # genau ein Block fmt: ffmpeg nimmt den ersten, hier zaehlte sonst der letzte (8 kHz, dann 48 kHz -> 6-fache
            # Dauer am Pruefer vorbei)
            if fmt is not None or size < 16 or body + 16 > len(b):
                raise Bad("format")
            tag, ch, rate, bits = _u16(b, body), _u16(b, body + 2), _u32(b, body + 4), _u16(b, body + 14)
            if tag != 1 or ch != 1 or bits != 16 or not 8000 <= rate <= 48000:
                raise Bad("format")
            fmt = rate * 2
        elif cid == b"data":
            if fmt is None:
                raise Bad("format")
            return max(0, len(b) - body) / fmt
        i = body + size + (size & 1)
    raise Bad("format")


_FRAME = (480, 960, 1920, 2880) * 3 + (480, 960) * 2 + (120, 240, 480, 960) * 4      # TOC config -> Samples (48 kHz)


def _opus_samples(p):
    """Samples (48 kHz) eines Opus-Pakets aus dem TOC-Byte (RFC 6716 3.1) - ohne zu decodieren."""
    if not p:
        return 0
    n = _FRAME[p[0] >> 3]
    code = p[0] & 3
    if code == 0:
        return n
    if code in (1, 2):
        return 2 * n
    return n * (p[1] & 0x3F) if len(p) > 1 else 0


def ogg_seconds(b):
    """Ogg/Opus (erste Seite OpusHead, 1-2 Kanaele, Mapping 0) -> Dauer in s. Alle Seiten werden der Reihe nach gelesen
    (ein Strom, lueckenlos); Dauer = das Groessere aus Summe der Paketdauern (TOC) und Granule der letzten Seite, beides
    abzueglich Pre-Skip - eine kleine Granule am Ende verkuerzt die Pruefung nicht. Bad(format) sonst."""
    n, i, serial, pkts, cur, granule = len(b), 0, None, 0, bytearray(), 0
    head, samples = None, 0
    for _ in range(100000):
        if i == n:
            break
        if i + 27 > n or b[i:i + 4] != b"OggS" or b[i + 4] != 0:
            raise Bad("format")
        typ, gp, ser, nseg = b[i + 5], int.from_bytes(b[i + 6:i + 14], "little", signed=True), _u32(b, i + 14), b[i + 26]
        if serial is None:
            if not typ & 2:
                raise Bad("format")
            serial = ser
        elif ser != serial:
            raise Bad("format")
        lace = b[i + 27:i + 27 + nseg]
        if len(lace) != nseg:
            raise Bad("format")
        j = i + 27 + nseg
        if j + sum(lace) > n:
            raise Bad("format")
        for v in lace:
            cur += b[j:j + v]
            j += v
            if v < 255:                                   # Paket zu Ende
                if pkts == 0:
                    if len(cur) < 19 or bytes(cur[:8]) != b"OpusHead" or cur[9] not in (1, 2) or cur[18] != 0:
                        raise Bad("format")
                    head = _u16(cur, 10)
                elif pkts >= 2:
                    samples += _opus_samples(cur)
                pkts += 1
                cur = bytearray()
        if gp >= 0:
            granule = gp
        i = j
    else:
        raise Bad("format")
    if head is None:
        raise Bad("format")
    return max(0, max(samples, granule) - head) / 48000


def check_audio(b, fmt, c):
    """-> Sekunden. Bad(empty|format|too_long). fmt: 'ogg' | 'wav' (aus dem Content-Type)."""
    if not b:
        raise Bad("empty")
    head = bytes(b[:4])
    if (fmt == "ogg" and head != b"OggS") or (fmt == "wav" and head != b"RIFF"):
        raise Bad("format")
    s = ogg_seconds(b) if fmt == "ogg" else wav_seconds(b)
    if s <= 0:
        raise Bad("empty")
    if s > MAX_S + MAX_SLACK:
        raise Bad("too_long", max_s=MAX_S)
    if fmt not in formats_of(c):
        raise Bad("format_service")
    return s


# --- Grenzen (im Speicher dieses Prozesses; ein Prozess je Server, run.py) ---------------------------------------------
_lock = threading.Lock()
_running = {}                                       # uid -> (Beginn (monotonic), Platz-Token der Anfrage)
_sent = {}                                          # uid -> [Zeitpunkte (testclock) der an den Dienst gegangenen Diktate]


def _prune(now):
    m = time.monotonic()
    for u in [u for u, (t0, _tok) in _running.items() if m - t0 > STALE]:
        _running.pop(u, None)
    for u in list(_sent):
        _sent[u] = [x for x in _sent[u] if now - x < 86400]
        if not _sent[u]:
            _sent.pop(u, None)


def take(uid):
    """Platz belegen -> Platz-Token (str) oder (code, retry_after). rate vor busy vor full. Das Token gibt free() mit:
    ein alter Handler, dessen Platz _prune schon geraeumt hat, gibt so nie den neuen Platz derselben Person frei."""
    now = testclock.now()
    with _lock:
        _prune(now)
        h = _sent.get(uid) or []
        hour = [x for x in h if now - x < 3600]
        if len(hour) >= RATE_H:
            return "rate", max(1, math.ceil(hour[-RATE_H] + 3600 - now))
        if len(h) >= RATE_D:
            return "rate", max(1, math.ceil(h[-RATE_D] + 86400 - now))
        if uid in _running:
            return "busy", None
        if len(_running) >= SERVER_MAX:
            return "full", FULL_RETRY
        tok = secrets.token_hex(8)
        _running[uid] = (time.monotonic(), tok)
    return tok


def free(uid, tok):
    with _lock:
        cur = _running.get(uid)
        if cur is not None and cur[1] == tok:
            _running.pop(uid, None)


def note_sent(uid):
    with _lock:
        _sent.setdefault(uid, []).append(testclock.now())


def reset():
    """Nur Tests (Neustart der Zaehler)."""
    with _lock:
        _running.clear()
        _sent.clear()


# --- Durchreiche -------------------------------------------------------------------------------------------------------
class Fail(Exception):
    def __init__(self, code, status=None):
        super().__init__(code)
        self.code, self.status = code, status


_MARK = re.compile(r"^\s*[\[(（][^\])）\n]{0,40}[\])）]\s*$")
_CTRL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")


def clean_text(s):
    """-> (text, cut). Steuerzeichen ausser Zeilenumbruch weg, Raender weg; nur eine Marke wie [BLANK_AUDIO] = leer."""
    s = _CTRL.sub(" ", str(s or "")).strip()
    s = re.sub(r"[  ]{2,}", " ", s)
    if _MARK.match(s):
        return "", False
    return (s[:TEXT_MAX].rstrip(), True) if len(s) > TEXT_MAX else (s, False)


def _post(c, data, fmt, lang, tmo):
    """Ein Aufruf des Dienstes (im Worker-Thread) -> Text. Fail(upstream|blocked|timeout)."""
    from saga.netguard import Blocked
    h = {"Accept": "application/json", "User-Agent": "Saga"}
    if c.get("api_key"):
        h["Authorization"] = "Bearer " + c["api_key"]
    files = {"file": ("audio." + fmt, data, "audio/ogg" if fmt == "ogg" else "audio/wav")}
    form = {"model": str(c.get("model") or ""), "language": lang, "response_format": "json", "temperature": "0"}
    try:
        cl = netguard.guarded_client(ip_ok(c), transport_kw={"verify": _verify()}, follow_redirects=False,
                                     event_hooks={"request": [_hook(c)]},
                                     timeout=httpx.Timeout(tmo, connect=min(tmo, 5.0)))
    except RuntimeError:
        raise Fail("blocked")
    try:
        with cl, cl.stream("POST", c["url"].rstrip("/") + PATH, data=form, files=files, headers=h) as r:
            if r.status_code != 200:
                raise Fail("upstream", r.status_code)
            buf = bytearray()
            for ch in r.iter_bytes():
                buf += ch
                if len(buf) > RESP_MAX:
                    raise Fail("upstream", 200)
    except httpx.TimeoutException:
        raise Fail("timeout")
    except httpx.UnsupportedProtocol:
        raise Fail("blocked")
    except httpx.HTTPError as ex:
        if isinstance(ex.__cause__, Blocked) or isinstance(ex, Blocked):
            raise Fail("blocked")
        raise Fail("upstream")
    except Blocked:
        raise Fail("blocked")
    try:
        d = json.loads(bytes(buf).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise Fail("upstream", 200)
    if not isinstance(d, dict) or not isinstance(d.get("text"), str):
        raise Fail("upstream", 200)
    return d["text"]


_POOL = [None]
_POOL_LOCK = threading.Lock()


def send(c, data, fmt, lang):
    """Gesamtfrist im Thread (wie memory.embed): httpx begrenzt nur je Lesevorgang. -> Text; Fail sonst."""
    tmo = float(max(TIMEOUT_MIN, min(TIMEOUT_MAX, int(c.get("timeout") or TIMEOUT_DEF))))
    with _POOL_LOCK:
        if _POOL[0] is None:
            _POOL[0] = concurrent.futures.ThreadPoolExecutor(SERVER_MAX + 2, thread_name_prefix="diktat")
        pool = _POOL[0]
    fut = pool.submit(_post, c, data, fmt, lang, tmo)
    try:
        return fut.result(timeout=tmo + 1)
    except concurrent.futures.TimeoutError:
        fut.cancel()
        raise Fail("timeout")


# --- Status (GET /api/ai/status -> stt) --------------------------------------------------------------------------------
def status_field(uid, conn=None):
    """-> None (nicht eingerichtet) | {on: false} (gesperrt) | {on, where, notice, max_s, max_bytes, formats, langs}."""
    c = cfg()
    if not configured(c):
        return None
    fm = formats_of(c)
    h = hint(c)
    if blocked_now(c) or not fm or not h:
        return {"on": False}
    from saga import ai_say
    try:
        persona = ai_say.persona_for(uid, conn)
    except Exception:  # noqa: BLE001 - Anrede unbekannt: du
        persona = None
    return {"on": True, "where": h, "notice": notice_text(h, persona), "max_s": MAX_S, "max_bytes": MAX_BYTES,
            "formats": fm, "langs": list(LANGS)}


# --- POST /api/ai/transcribe -------------------------------------------------------------------------------------------
def _err(status, code, headers=None, **extra):
    body = {"error": message(code, **extra)}
    if code not in ("key",):
        body["code"] = "format" if code == "format_service" else code
    body.update({k: v for k, v in extra.items() if k in ("max_s", "retry_after")})
    return JSONResponse(body, status_code=status, headers=dict(NO_STORE, **(headers or {})))


def _precheck(uid):
    """-> None oder Antwort: Faehigkeit ai, eingerichtet, Einwilligung, Schalter der Person."""
    from saga import ai_saga, caps
    if not caps.can(uid, "ai"):
        return _err(403, "ai")
    if not configured(cfg()):
        return _err(404, "off")
    if not ai_saga.consent_ok(uid):
        return ai_saga.consent_needed()
    with _db() as conn:
        if ai_saga._load_prefs(conn, uid)["dictation"] == "device":
            return _err(409, "device_only")
    return None


def _lang(request, c):
    from saga import i18n
    q = request.query_params.get("lang")
    if q is not None:
        return q if q in LANGS else None
    if c.get("language") in LANGS:
        return c["language"]
    cur = i18n.get_lang()
    return cur if cur in LANGS else "en"


def _measure(uid, text, secs):
    from saga import ai_units
    try:
        ai_units.measure(uid, "stt", ai_units.units_of(len(text)), real_in=round(secs))
    except Exception as ex:  # noqa: BLE001 - Messwert, nie das Diktat scheitern lassen
        print(f"stt: Messwert {type(ex).__name__}", flush=True)


@router.post("/api/ai/transcribe")
async def api_transcribe(request: Request):
    """Rohe Aufnahme (audio/ogg | audio/wav, Query lang) -> {text, dur, lang, cut}. Vertrag APP-API-KI 15.1."""
    if (request.headers.get("authorization") or "").strip():         # RT-42: kein API-Schluessel, auch hkd_ nicht
        return _err(403, "key")
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    pre = await asyncio.to_thread(_precheck, uid)
    if pre is not None:
        return pre
    c = cfg()
    lang = _lang(request, c)
    if lang is None:
        return _err(400, "lang")
    fmt = CTYPES.get((request.headers.get("content-type") or "").split(";")[0].strip().lower())
    if fmt is None:
        return _err(400, "format")
    try:
        clen = int(request.headers.get("content-length") or 0)
        if clen > MAX_BYTES:
            return _err(413, "too_big")
    except ValueError:
        return _err(400, "format")
    slot = take(uid)
    if isinstance(slot, tuple):
        code, ra = slot
        hdr = {"Retry-After": str(ra)} if ra else None
        return _err({"rate": 429, "busy": 409, "full": 503}[code], code, headers=hdr, **({"retry_after": ra} if ra else {}))
    buf = bytearray()
    t0 = time.perf_counter()

    async def read():
        async for chunk in request.stream():
            if len(buf) + len(chunk) > MAX_BYTES:
                return False
            buf.extend(chunk)
        return True
    try:
        try:
            limit = read_limit(max(0, clen))
            whole = await asyncio.wait_for(read(), limit)           # Gesamtfrist: ein troepfelnder Upload haelt keinen Platz
        except asyncio.TimeoutError:
            note_sent(uid)                                          # zaehlt wie ein Versuch (30/h, 120/Tag): kein Dauerblockieren
            print(f"stt: 408 slow {round(len(buf) / 1024)} KB in {limit:.0f} s", flush=True)
            return _err(408, "slow", headers={"Connection": "close"})
        if not whole:
            return _err(413, "too_big")
        try:
            secs = check_audio(buf, fmt, c)
        except Bad as ex:
            return _err(400, ex.code, **ex.kw)
        c = cfg()                                                       # frisch: left_home kann sich geaendert haben
        if blocked_now(c):
            print("stt: 502 blocked (Ziel nicht erlaubt, nichts gesendet)", flush=True)
            return _err(502, "blocked")
        kb = round(len(buf) / 1024)
        note_sent(uid)
        data = bytes(buf)
        try:
            raw = await asyncio.to_thread(send, c, data, fmt, lang)
        except Fail as ex:
            st = {"blocked": 502, "timeout": 504}.get(ex.code, 502)
            print(f"stt: {st} {ex.code}{' ' + str(ex.status) if ex.status else ''} {secs:.1f} s {kb} KB "
                  f"{int((time.perf_counter() - t0) * 1000)} ms", flush=True)
            return _err(st, ex.code)
        finally:
            data = None
        text, cut = clean_text(raw)
        raw = None
        print(f"stt: 200 {secs:.1f} s {kb} KB {int((time.perf_counter() - t0) * 1000)} ms", flush=True)
        await asyncio.to_thread(_measure, uid, text, secs)
        return JSONResponse({"text": text, "dur": round(secs, 1), "lang": lang, "cut": cut}, headers=NO_STORE)
    except Exception as ex:  # noqa: BLE001 - nie Inhalte loggen
        if type(ex).__name__ == "BodyTooLarge":                         # BodyLimit ersetzt die Antwort ohnehin durch 413
            return _err(413, "too_big")
        if type(ex).__name__ == "ClientDisconnect":                     # Verbindung weg: niemand liest die Antwort
            return JSONResponse({"error": "disconnected"}, status_code=400, headers=NO_STORE)
        print(f"stt: 500 {type(ex).__name__}", flush=True)
        return JSONResponse({"error": message("upstream"), "code": "upstream"}, status_code=500, headers=NO_STORE)
    finally:
        buf[:] = bytes(len(buf))                                        # best effort: Ton im Speicher ueberschreiben
        free(uid, slot)


# --- Admin (JSON auf dem Admin-Port, admin.py /api/ai/stt*) ---------------------------------------------------------------
def admin_view():
    c = cfg()
    s = {k: c.get(k) for k in ("enabled", "url", "model", "language", "cloud", "timeout", "note")}
    s["formats"] = formats_of(c)
    s["has_key"] = bool(c.get("api_key"))
    s["pinned"] = list(c.get("pinned") or [])
    h = hint(c) if c.get("url") else None
    return {"settings": s, "configured": configured(c), "where": h, "blocked": blocked_now(c) if configured(c) else False}


def admin_values(b, cur):
    """JSON des Admins -> (werte, fehler). Leerer Schluessel = bisheriger."""
    from saga import ai_saga
    if not isinstance(b, dict):
        return None, t("ungültige Anfrage")
    url = str(b.get("url") or "").strip()
    if url:
        url, err = norm_url(url)
        if err:
            return None, err
    lang = b.get("language") if b.get("language") in LANGS else ""
    try:
        tmo = max(TIMEOUT_MIN, min(TIMEOUT_MAX, int(b.get("timeout") if b.get("timeout") is not None else cur.get("timeout") or TIMEOUT_DEF)))
    except (TypeError, ValueError):
        tmo = TIMEOUT_DEF
    v = {"enabled": b.get("enabled") is True, "url": url or "", "model": str(b.get("model") or "").strip()[:200] or "whisper-1",
         "api_key": str(b.get("api_key") or "").strip()[:500], "language": lang, "cloud": b.get("cloud") is True,
         "timeout": tmo, "note": ai_saga.clean_note(b.get("note")),
         "formats": formats_of(cur) if (url or "") == cur.get("url") else list(FORMATS),
         "pinned": list(cur.get("pinned") or [])}
    if v["enabled"] and not v["url"]:
        return None, t("Zum Einschalten die Adresse des Diktat-Dienstes angeben.")
    return v, None


def check_pins(v):
    """Adresse aufloesen und gegen die Regeln pruefen -> (ips, fehler)."""
    from saga import ai, ai_saga
    ips, err = ai.resolve(v["url"])
    if err:
        return ips, err
    if any(_ipn(ip) in METADATA for ip in ips):
        return ips, t("Diese Adresse ist nicht erlaubt.")
    if not v.get("cloud") and not ai_saga.home_ips(ips):
        return ips, t("Nur Adressen im eigenen Netz – für Cloud-Dienste „Cloud erlauben“ einschalten.")
    return ips, None


def admin_save(b):
    """-> (status, dict)."""
    cur = cfg()
    v, err = admin_values(b, cur)
    if err:
        return 400, {"error": err}
    if v["url"]:
        ips, err = check_pins(v)
        if err:
            return 400, {"error": err}
        if sorted(ips) != sorted(cur.get("pinned") or []):
            v["formats"] = list(FORMATS)
        v["pinned"] = ips
    else:
        v["pinned"] = []
    auth.save_setting("ai_stt", v)
    if b.get("clear_key") is True:
        _raw_update(lambda st: st.update(api_key=""))
    return 200, admin_view()


def _raw_update(fn):
    """Gespeicherte Einstellung roh aendern (Geheimnis bleibt verschluesselt)."""
    with _db() as c, c:
        r = c.execute("SELECT v FROM setting WHERE k='ai_stt'").fetchone()
        if not r:
            return
        st = json.loads(r[0])
        fn(st)
        c.execute("UPDATE setting SET v=? WHERE k='ai_stt'", (json.dumps(st, ensure_ascii=False),))
    auth._settings_cache["v"] = None


def silence(fmt, s=1.0):
    """Stille als WAV (16 kHz) bzw. Ogg/Opus (20-ms-Pakete) - fuer den Verbindungstest."""
    if fmt == "wav":
        n = int(16000 * s) * 2
        return (b"RIFF" + (36 + n).to_bytes(4, "little") + b"WAVEfmt " + (16).to_bytes(4, "little") + (1).to_bytes(2, "little")
                + (1).to_bytes(2, "little") + (16000).to_bytes(4, "little") + (32000).to_bytes(4, "little")
                + (2).to_bytes(2, "little") + (16).to_bytes(2, "little") + b"data" + n.to_bytes(4, "little") + bytes(n))
    return ogg_opus(s)


def _crc(data):
    r = 0
    for x in data:
        r ^= x << 24
        for _ in range(8):
            r = ((r << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if r & 0x80000000 else (r << 1) & 0xFFFFFFFF
    return r


def _page(serial, seq, gp, typ, packets):
    lace = bytearray()
    for p in packets:
        n = len(p)
        lace += bytes([255] * (n // 255)) + bytes([n % 255])
    h = bytearray(b"OggS\x00" + bytes([typ]) + int(gp).to_bytes(8, "little", signed=True) + serial.to_bytes(4, "little")
                  + seq.to_bytes(4, "little") + bytes(4) + bytes([len(lace)]) + bytes(lace))
    body = b"".join(packets)
    h[22:26] = _crc(bytes(h) + body).to_bytes(4, "little")
    return bytes(h) + body


def ogg_opus(s, tags=b"", preskip=312):
    """Ogg/Opus mit Stille (CELT 20 ms, TOC 0xF8) - s Sekunden; tags: zusaetzlicher Kommentar in OpusTags."""
    head = b"OpusHead" + bytes([1, 1]) + preskip.to_bytes(2, "little") + (48000).to_bytes(4, "little") + bytes(3)
    vendor = b"Saga"
    com = [tags] if tags else []
    otags = b"OpusTags" + len(vendor).to_bytes(4, "little") + vendor + len(com).to_bytes(4, "little") + b"".join(
        len(x).to_bytes(4, "little") + x for x in com)
    out = [_page(0x5A4741, 0, 0, 2, [head]), _page(0x5A4741, 1, 0, 0, [otags])]
    n, seq, gp = int(round(s * 50)), 2, preskip
    pk = b"\xf8\xff\xfe"
    while n > 0:
        k = min(n, 50)
        gp += k * 960
        n -= k
        out.append(_page(0x5A4741, seq, gp, 4 if n == 0 else 0, [pk] * k))
        seq += 1
    return b"".join(out)


def admin_test(b):
    """Verbindungstest mit den Werten aus dem Formular (ungespeichert) -> {ok, lines, formats}. Schickt je 1 s Stille als
    WAV und Ogg; stimmen Adresse und festgehaltene IPs mit der gespeicherten Einstellung ueberein, uebernimmt Saga die
    angenommenen Formate dort (stt.formats)."""
    cur = cfg()
    v, err = admin_values(dict(b or {}, enabled=True), cur)
    if err:
        return {"ok": False, "lines": [f"✗ {err}"]}
    if not v["api_key"]:
        v["api_key"] = cur.get("api_key") or ""
    if not v["url"]:
        return {"ok": False, "lines": ["✗ " + t("Zum Einschalten die Adresse des Diktat-Dienstes angeben.")]}
    ips, err = check_pins(v)
    if err:
        return {"ok": False, "lines": [f"✗ {err}"]}
    v["pinned"] = ips
    v["formats"] = list(FORMATS)
    v.pop("left_home", None)
    lines = [t("✓ {host} aufgelöst: {ips}", host=urlparse(v["url"]).hostname, ips=", ".join(ips[:4]))]
    h = hint(v)
    if h and h["net"] == "internet":
        lines.append(t("Hinweis: Der Dienst liegt im Internet – Aufnahmen verlassen dann den Server."))
    ok = []
    for fmt in ("wav", "ogg"):
        t0 = time.perf_counter()
        try:
            send(v, silence(fmt), fmt, v["language"] or "de")
        except Fail as ex:
            why = {"timeout": t("keine Antwort in der Frist"), "blocked": t("Verbindung nicht erlaubt")}.get(ex.code) or (
                t("Fehler {code}", code=ex.status) if ex.status else t("nicht erreichbar oder unerwartete Antwort"))
            lines.append(t("✗ {fmt}: {why}", fmt=fmt.upper(), why=why))
            continue
        ok.append(fmt)
        lines.append(t("✓ {fmt} angenommen ({ms} ms)", fmt=fmt.upper(), ms=int((time.perf_counter() - t0) * 1000)))
    fm = [f for f in FORMATS if f in ok]
    if fm and v["url"] == cur.get("url") and sorted(ips) == sorted(cur.get("pinned") or []):
        _raw_update(lambda st: st.update(formats=fm))
        lines.append(t("Gespeichert: Saga schickt nur noch {fmts}.", fmts=", ".join(x.upper() for x in fm)))
    elif fm:
        lines.append(t("Nach dem Speichern noch einmal testen, dann merkt sich Saga die Formate."))
    return {"ok": bool(fm), "lines": lines, "formats": fm}
