"""Anruf-Diagnose (2.5, Analyse 1.11.): POST /api/calls/diag - Clients (Web, iOS, mac, Android, Linux) melden waehrend eines
Anrufs gesammelte Messwerte (Empfang je Quelle, Senden je Simulcast-Schicht, Schicht-/Weg-/Qualitaetswechsel).

Vertrag: docs/APP-API.md "Anruf-Diagnose". Keine Datenbank: je POST genau EINE kompakte Zeile im Server-Log (Praefix
"calldiag", per docker logs zu sehen), Konto nur als kurzer Hash, keine IP-Adressen, keine Namen. Felder werden streng
geprueft (Typ, Laenge, Wertebereich), unbekannte Felder verworfen. Nur Teilnehmende des Anrufs duerfen senden (sonst 404 wie
bei den uebrigen Anruf-APIs), hoechstens 1 POST je 15 s je Konto und Anruf (429), Body hoechstens 32 KB (413).
Admin-Schalter calls.diag (Standard an): aus -> 404 und kein Feature calldiag1 in /api/version."""
import hashlib
import hmac
import json
import math
import re
import time

from fastapi import APIRouter, Request

from db import connect as _db
from saga import calls, chat
from saga.core import SECRET, _bad, parse_json, t

router = APIRouter()

FEATURE = "calldiag1"
BODY_MAX = 32 * 1024
SAMPLES_MAX, EVENTS_MAX = 200, 100
RATE_S = 15.0
T_MAX = calls.MAX_S * 1000 + 3600 * 1000            # ms seit Anrufbeginn (laengster Anruf + Puffer)
NUM_MAX = 1e9
PLATS = ("web", "ios", "mac", "android", "linux")
PATHS = ("horn", "p2p")
TRANSPORTS = ("udp", "tcp", "tls", "?")
DIRS = ("in", "out")
RIDS = ("l", "m", "h", "")
QLRS = ("none", "cpu", "bandwidth", "other", "")
KINDS = ("layers", "thermal", "switch", "quality", "path")
NUMS = ("fps", "h", "kbps", "lost", "nack", "pli", "freeze", "kf", "rtt",
        # 2.5 (Labor 2.11., Stottern durch Geraete-CPU): out - qcpu/qbw = ms, die der Kodierer seit der letzten Probe durch
        # CPU bzw. Bandbreite begrenzt war (qualityLimitationDurations), encms = mittlere Kodierzeit je Rahmen, fdrop = Rahmen
        # der Quelle, die der Kodierer nicht kodiert hat; in - fdrop = verworfene Rahmen (framesDropped), decms = mittlere
        # Dekodierzeit je Rahmen, fzms = Dauer der Standbilder (totalFreezesDuration)
        "qcpu", "qbw", "encms", "fdrop", "decms", "fzms")
_HEX32 = re.compile(r"[0-9a-f]{32}")
_APP = re.compile(r"[A-Za-z0-9._+\-]{0,40}")
_NODE = re.compile(r"[A-Za-z0-9.\-:\[\]]{0,253}")
_SRC = re.compile(r"[A-Za-z0-9_\-:.]{0,16}")
_ENC = re.compile(r"[A-Za-z0-9 _()\[\].,:+/\-]{0,60}")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_last = {}                                          # (konto, anruf) -> Zeitpunkt des letzten angenommenen POST


def enabled():
    return bool(calls.cfg().get("diag", True)) and calls.enabled()


def features():
    return [FEATURE] if enabled() else []


def acct_hash(uid):
    """Kurzer, nicht umkehrbarer Hash des Kontos (stabil je Server) - fuer die Log-Zeile statt des Namens."""
    return hmac.new(SECRET.encode(), ("calldiag|" + str(uid)).encode(), hashlib.sha256).hexdigest()[:10]


class Bad(ValueError):
    pass


def _str(d, k, rx=None, enum=None, need=True):
    v = d.get(k)
    if v is None and not need:
        return None
    if not isinstance(v, str):
        raise Bad(k)
    if enum is not None and v not in enum:
        raise Bad(k)
    if rx is not None and not rx.fullmatch(v):
        raise Bad(k)
    return v


def _int(v, k, lo, hi):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v != int(v) or not lo <= v <= hi:
        raise Bad(k)
    return int(v)


def _num(v, k):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= NUM_MAX:
        raise Bad(k)
    return int(v) if v == int(v) else round(float(v), 2)


def clean(b):
    """Body pruefen -> bereinigtes dict (nur bekannte Felder). Bad(feld) bei falschem Typ/Wert."""
    if not isinstance(b, dict):
        raise Bad("body")
    if _int(b.get("v"), "v", 1, 1) != 1:
        raise Bad("v")
    out = {"v": 1, "call": _str(b, "call", _HEX32), "plat": _str(b, "plat", enum=PLATS), "app": _str(b, "app", _APP),
           "path": _str(b, "path", enum=PATHS), "transport": _str(b, "transport", enum=TRANSPORTS), "node": _str(b, "node", _NODE)}
    sm, ev = b.get("samples", []), b.get("events", [])
    if not isinstance(sm, list) or len(sm) > SAMPLES_MAX:
        raise Bad("samples")
    if not isinstance(ev, list) or len(ev) > EVENTS_MAX:
        raise Bad("events")
    out["samples"] = []
    for i, s in enumerate(sm):
        if not isinstance(s, dict):
            raise Bad(f"samples[{i}]")
        x = {"t": _int(s.get("t"), f"samples[{i}].t", 0, T_MAX), "dir": _str(s, "dir", enum=DIRS),
             "src": _str(s, "src", _SRC, need=False) or "", "rid": _str(s, "rid", enum=RIDS, need=False) or ""}
        for k in NUMS:
            if s.get(k) is not None:
                x[k] = _num(s[k], f"samples[{i}].{k}")
        q = _str(s, "qlr", enum=QLRS, need=False)
        if q:
            x["qlr"] = q
        e = _str(s, "enc", _ENC, need=False)
        if e:
            x["enc"] = e
        out["samples"].append(x)
    out["events"] = []
    for i, e in enumerate(ev):
        if not isinstance(e, dict):
            raise Bad(f"events[{i}]")
        d = e.get("d", "")
        if not isinstance(d, str) or len(d) > 120:
            raise Bad(f"events[{i}].d")
        out["events"].append({"t": _int(e.get("t"), f"events[{i}].t", 0, T_MAX), "k": _str(e, "k", enum=KINDS), "d": _CTRL.sub(" ", d)})
    return out


def _rate_ok(uid, cid, now):
    k = (uid, cid)
    if now - _last.get(k, 0) < RATE_S:
        return False
    if len(_last) > 20000:
        for kk in [kk for kk, v in _last.items() if now - v > RATE_S]:
            _last.pop(kk, None)
    _last[k] = now
    return True


def log_line(uid, d):
    """Eine Zeile: calldiag <json> - Konto als Hash, Anruf-ID gekuerzt (zum Zuordnen der Meldungen beider Seiten)."""
    rec = {"acct": acct_hash(uid), "call": d["call"][:12], **{k: d[k] for k in ("plat", "app", "path", "transport", "node")},
           "n": [len(d["samples"]), len(d["events"])], "samples": d["samples"], "events": d["events"]}
    return "calldiag " + json.dumps(rec, separators=(",", ":"), ensure_ascii=False)


@router.post("/api/calls/diag")
async def api_diag(request: Request):
    """Diagnose eines Anrufs (Vertrag docs/APP-API.md "Anruf-Diagnose") -> {ok: true}. 404 aus/unbekannt, 429, 413, 400."""
    if not enabled():
        return _bad("not found", 404)
    uid, err = calls._me(request)
    if err:
        return err
    try:
        n = int(request.headers.get("content-length") or 0)
    except ValueError:
        n = 0
    if n > BODY_MAX:
        return _bad("too large", 413)
    raw = await request.body()
    if len(raw) > BODY_MAX:
        return _bad("too large", 413)
    try:
        d = clean(parse_json(raw))
    except Bad as e:
        return _bad(t("ungültige Angabe") + f" ({e})")
    except Exception:  # noqa: BLE001 - kein JSON
        return _bad(t("ungültige Angabe"))
    with _db() as c:
        _call, err = calls._mine(c, uid, d["call"])     # Teilnehmende wie bei den uebrigen Anruf-APIs, sonst 404
        if err:
            return err
    if not _rate_ok(uid, d["call"], time.time()):
        return chat._no_store({"error": t("Zu viele Anfragen – bitte kurz warten."), "retry_s": int(RATE_S)}, 429)
    print(log_line(uid, d), flush=True)
    return chat._no_store({"ok": True})
