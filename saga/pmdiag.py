"""Editor-Diagnose (2.5, Tiptap-Umbau T6): POST /api/diag - der Tiptap-Editor meldet beim Speichern (Live: kurz nach
Aenderungen) Bloecke, die der Nutzer NICHT bearbeitet hat, deren serialisierte Form aber von der geladenen abweicht
(Round-Trip-Abweichung = Luecke im Adapter static/pm-adapter.js bzw. im Schema).

Vertrag: docs/APP-API.md "Editor-Diagnose". Je POST genau EINE Zeile im Server-Log (Praefix "pmdiag"; dazu derselbe
Inhalt in diag_event fuer Admin -> Diagnose, saga/diagstore.py), Konto nur als kurzer Hash. Gemeldet werden nur Blocktyp, Feldpfad (Feldnamen und Indizes, z. B. data.content[2][1]) und
Art der Abweichung - nie Inhalt, Text, Block-IDs, Dateinamen oder Adressen. Felder werden streng geprueft (Zeichen,
Laenge), unbekannte Felder verworfen. Hoechstens 1 POST je 55 s je Konto (429; der Client sendet hoechstens alle 60 s),
hoechstens 20 Eintraege, Body hoechstens 8 KB (413). Admin-Schalter editor.diag (Standard an): aus -> 404 und kein
Feature pmdiag1 in /api/version. Konten mit Editor.js (kein Tiptap) -> 403."""
import hashlib
import hmac
import json
import re
import time

import auth
from fastapi import APIRouter, Request

from saga import diagstore
from saga.core import SECRET, _api_guard, _bad, editor_engine, parse_json, t
from saga.chat import _no_store

router = APIRouter()

FEATURE = "pmdiag1"
BODY_MAX = 8 * 1024
ITEMS_MAX = 20
RATE_S = 55.0
MODES = ("save", "live")
KINDS = ("pass", "norm", "fresh", "err")   # Durchreichung verloren | Normalform != Original (ueber erlaubte Abweichungen hinaus) | Editor-Knoten != Normalform | Fehler
_TYPE = re.compile(r"[a-z][a-z0-9_]{0,23}|\?")
# Pfad: Feldnamen (Zeichen wie Bezeichner, sonst *) und Indizes - z. B. data.content[2][1], data.items[0].meta, tunes.*
_SEG = r"(?:[A-Za-z_][A-Za-z0-9_]{0,31}|\*)"
_PATH = re.compile(rf"(?:{_SEG}(?:\.{_SEG}|\[\d{{1,5}}\])*)?")
_AV = re.compile(r"[A-Za-z0-9._\-]{0,40}")
PATH_MAX = 160
_last = {}                                          # Konto -> Zeitpunkt des letzten angenommenen POST


def cfg():
    return auth.settings().get("editor") or {}


def enabled():
    return bool(cfg().get("diag", True))


def features():
    return [FEATURE] if enabled() else []


def acct_hash(uid):
    """Kurzer, nicht umkehrbarer Hash des Kontos (stabil je Server) - fuer die Log-Zeile statt des Namens."""
    return hmac.new(SECRET.encode(), ("pmdiag|" + str(uid)).encode(), hashlib.sha256).hexdigest()[:10]


class Bad(ValueError):
    pass


def _str(d, k, rx=None, enum=None, mx=None):
    v = d.get(k)
    if not isinstance(v, str) or (mx is not None and len(v) > mx):
        raise Bad(k)
    if enum is not None and v not in enum:
        raise Bad(k)
    if rx is not None and not rx.fullmatch(v):
        raise Bad(k)
    return v


def clean(b):
    """Body pruefen -> bereinigtes dict (nur bekannte Felder, Eintraege dedupliziert). Bad(feld) bei falschem Typ/Wert."""
    if not isinstance(b, dict):
        raise Bad("body")
    if b.get("v") != 1 or isinstance(b.get("v"), bool):
        raise Bad("v")
    sv = b.get("sv")
    if isinstance(sv, bool) or not isinstance(sv, int) or not 1 <= sv <= 999:
        raise Bad("sv")
    out = {"mode": _str(b, "mode", enum=MODES), "sv": sv, "av": _str(b, "av", _AV) if b.get("av") is not None else ""}
    it = b.get("items")
    if not isinstance(it, list) or not it or len(it) > ITEMS_MAX:
        raise Bad("items")
    seen, items = set(), []
    for i, x in enumerate(it):
        if not isinstance(x, dict):
            raise Bad(f"items[{i}]")
        e = (_str(x, "t", _TYPE), _str(x, "p", _PATH, mx=PATH_MAX), _str(x, "k", enum=KINDS))
        if e not in seen:
            seen.add(e)
            items.append({"t": e[0], "p": e[1], "k": e[2]})
    out["items"] = items
    return out


def _rate_ok(uid, now):
    if now - _last.get(uid, 0) < RATE_S:
        return False
    if len(_last) > 20000:
        for k in [k for k, v in _last.items() if now - v > RATE_S]:
            _last.pop(k, None)
    _last[uid] = now
    return True


def log_rec(uid, d):
    return {"acct": acct_hash(uid), "mode": d["mode"], "sv": d["sv"], "av": d["av"], "n": len(d["items"]), "items": d["items"]}


def log_line(uid, d, rec=None):
    """Eine Zeile: pmdiag <json> - Konto als Hash, Modus, Schema-Version, Asset-Stand, Eintraege (Typ, Pfad, Art)."""
    return "pmdiag " + json.dumps(rec or log_rec(uid, d), separators=(",", ":"), ensure_ascii=True)


@router.post("/api/diag")
async def api_diag(request: Request):
    """Round-Trip-Abweichungen des Tiptap-Editors (Vertrag docs/APP-API.md "Editor-Diagnose") -> {ok: true}.
    404 Schalter aus, 401 ohne Anmeldung, 403 Konto ohne Tiptap, 413, 429, 400."""
    if not enabled():
        return _bad("not found", 404)
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    if editor_engine(uid) != "tiptap":
        return _bad(t("Nur für den neuen Editor."), 403)
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
    if not _rate_ok(uid, time.time()):
        return _no_store({"error": t("Zu viele Anfragen – bitte kurz warten."), "retry_s": int(RATE_S)}, 429)
    rec = log_rec(uid, d)
    print(log_line(uid, d, rec), flush=True)
    diagstore.record("pmdiag", rec, client=("editor " + d["av"]).strip())   # Admin -> Diagnose (zusaetzlich, blockiert nie)
    return _no_store({"ok": True})
