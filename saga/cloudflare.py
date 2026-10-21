"""Cloudflare-DNS fuer Gjallarhorn-Knoten (Saga 2.5, Paket P7; Konzept v2 §2.8).

Nur so viel API wie noetig: Token pruefen (GET /user/tokens/verify), Zonen lesen (GET /zones), DNS-Eintraege lesen,
anlegen, aendern und loeschen (/zones/{id}/dns_records). Jeder Eintrag, den Saga anlegt, traegt den Kommentar
saga-horn:<node_id>; geaendert und geloescht werden nur Eintraege mit genau diesem Kommentar. A und AAAA immer mit
proxied=false (graue Wolke) und TTL 120.

Token: im Admin-Bereich (Anrufe -> DNS), verschluesselt in den Einstellungen (Bereich horn_cf, Fernet "enc:"), angezeigt
nur maskiert (••••••••3f2a). Er verlaesst Saga nie - Knoten bekommen fuer ACME DNS-01 nur den gesetzten TXT-Wert
(saga/horn.py, acme_txt). Pruefung beim Speichern: Token aktiv, genau eine Zone sichtbar (sonst Warnung "zu viele
Rechte"), Eintraege lesbar und ein Probe-TXT anlegbar und wieder loeschbar ("DNS bearbeiten").

SAGA_CF_API (nur Tests): andere API-Basis, z. B. ein lokaler Fake. Standard https://api.cloudflare.com/client/v4."""
import json
import os
import secrets
import time

import httpx

import auth
from db import connect as _db

API = os.environ.get("SAGA_CF_API", "https://api.cloudflare.com/client/v4").rstrip("/")
TTL = 120
TIMEOUT = 10.0


class CFError(Exception):
    def __init__(self, msg, code=0):
        super().__init__(msg)
        self.code = code


def comment(node_id):
    return "saga-horn:" + str(node_id)


def mask(tok):
    return ("••••••••" + tok[-4:]) if tok else ""


def conf():
    """{token, zone_id, zone, check} (Token entschluesselt)."""
    return auth.settings().get("horn_cf") or {}


def configured():
    c = conf()
    return bool(c.get("token") and c.get("zone_id"))


def _req(tok, method, path, **kw):
    try:
        r = httpx.request(method, API + path, headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json",
                                                       "User-Agent": "Saga-Gjallarhorn"}, timeout=TIMEOUT, **kw)
    except httpx.HTTPError as ex:
        raise CFError("Cloudflare nicht erreichbar: " + type(ex).__name__) from None
    try:
        d = r.json()
    except ValueError:
        d = {}
    if r.status_code >= 400 or not d.get("success", False):
        errs = d.get("errors") or []
        msg = "; ".join(str(e.get("message") or e) for e in errs if isinstance(e, dict))[:300] or f"HTTP {r.status_code}"
        raise CFError(msg, r.status_code)
    return d


def check(tok, zone_id=""):
    """Token pruefen -> {ok, active, zones: [{id, name}], zone_id, zone, dns_edit, warn, err}. Kein Speichern."""
    out = {"ok": False, "active": False, "zones": [], "zone_id": "", "zone": "", "dns_edit": False, "warn": "", "err": "",
           "at": time.time()}
    try:
        v = _req(tok, "GET", "/user/tokens/verify")
        out["active"] = (v.get("result") or {}).get("status") == "active"
        if not out["active"]:
            out["err"] = "inaktiv"
            return out
        zs = _req(tok, "GET", "/zones", params={"per_page": 50}).get("result") or []
        out["zones"] = [{"id": str(z.get("id")), "name": str(z.get("name"))} for z in zs if z.get("id")]
        if not out["zones"]:
            out["err"] = "keine Zone"
            return out
        if len(out["zones"]) > 1:
            out["warn"] = "zones"                     # Token sieht mehr als eine Zone: zu viele Rechte (Warnung)
        z = next((z for z in out["zones"] if z["id"] == zone_id), None) or (out["zones"][0] if len(out["zones"]) == 1 else None)
        if z is None:
            out["err"] = "zone_waehlen"
            return out
        out["zone_id"], out["zone"] = z["id"], z["name"]
        _req(tok, "GET", f"/zones/{z['id']}/dns_records", params={"per_page": 1})
        # Probe: kurzer TXT-Eintrag, sofort wieder weg - prueft "DNS bearbeiten" wirklich
        probe = _req(tok, "POST", f"/zones/{z['id']}/dns_records",
                     json={"type": "TXT", "name": f"_saga-probe-{secrets.token_hex(4)}.{z['name']}", "content": "saga-horn-probe",
                           "ttl": TTL, "comment": comment("probe")})
        rid = (probe.get("result") or {}).get("id")
        if rid:
            _req(tok, "DELETE", f"/zones/{z['id']}/dns_records/{rid}")
        out["dns_edit"] = True
        out["ok"] = True
    except CFError as ex:
        out["err"] = str(ex)
    return out


def save(tok, zone_id=""):
    """Token pruefen und speichern (nur wenn gueltig und DNS bearbeitbar) -> Pruefergebnis."""
    r = check(tok, zone_id)
    if r["ok"]:
        auth.save_setting("horn_cf", {"token": tok, "zone_id": r["zone_id"], "zone": r["zone"], "check": _public(r)})
    return r


def _public(r):
    return {k: r.get(k) for k in ("ok", "active", "zone", "dns_edit", "warn", "err", "at")} | {"zones": len(r.get("zones") or [])}


def clear():
    """Token entfernen (leere Geheimnisse wuerden save_setting sonst behalten)."""
    with _db() as c, c:
        c.execute("INSERT OR REPLACE INTO setting (k, v) VALUES ('horn_cf', ?)",
                  (json.dumps({"token": "", "zone_id": "", "zone": "", "check": {}}),))
    auth._settings_cache["v"] = None


def in_zone(name, zone=None):
    z = (zone if zone is not None else conf().get("zone") or "").lower()
    n = (name or "").lower().rstrip(".")
    return bool(z) and (n == z or n.endswith("." + z))


# --- Eintraege ------------------------------------------------------------------------------------------------------
def _ctx():
    c = conf()
    if not c.get("token") or not c.get("zone_id"):
        raise CFError("kein Cloudflare-Token")
    return c["token"], c["zone_id"]


def records(node_id, name=None, typ=None):
    """Eintraege mit Kommentar saga-horn:<node_id> (nur genau dieser Kommentar)."""
    tok, zid = _ctx()
    params = {"per_page": 100, "comment.exact": comment(node_id)}
    if name:
        params["name"] = name
    if typ:
        params["type"] = typ
    rs = _req(tok, "GET", f"/zones/{zid}/dns_records", params=params).get("result") or []
    return [r for r in rs if r.get("comment") == comment(node_id)]


def upsert(node_id, name, typ, content):
    """A/AAAA/TXT anlegen bzw. aendern (graue Wolke, TTL 120). content leer: Eintraege dieses Typs loeschen. -> id."""
    tok, zid = _ctx()
    have = records(node_id, name, typ)
    if not content:
        for r in have:
            _req(tok, "DELETE", f"/zones/{zid}/dns_records/{r['id']}")
        return None
    body = {"type": typ, "name": name, "content": content, "ttl": TTL, "comment": comment(node_id)}
    if typ in ("A", "AAAA"):
        body["proxied"] = False
    if have and typ != "TXT":
        r = have[0]
        if r.get("content") != content or r.get("proxied") or r.get("ttl") != TTL:
            _req(tok, "PATCH", f"/zones/{zid}/dns_records/{r['id']}", json=body)
        for x in have[1:]:
            _req(tok, "DELETE", f"/zones/{zid}/dns_records/{x['id']}")
        return r["id"]
    d = _req(tok, "POST", f"/zones/{zid}/dns_records", json=body)
    return (d.get("result") or {}).get("id")


def delete_all(node_id, name=None, typ=None):
    """Alle Eintraege mit Kommentar saga-horn:<node_id> loeschen -> Anzahl."""
    tok, zid = _ctx()
    n = 0
    for r in records(node_id, name, typ):
        _req(tok, "DELETE", f"/zones/{zid}/dns_records/{r['id']}")
        n += 1
    return n
