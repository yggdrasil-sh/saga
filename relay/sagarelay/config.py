"""Konfiguration des Push-Relais (docs/PUSH-RELAY.md).

RELAY_CONFIG (Standard /data/relay.json):
  {"teams": [{"team_id": "ABCDE12345", "key_id": "KEY1234567", "p8_file": "/run/secrets/apns.p8",
              "bundles": ["org.example.saga"], "widgets": false}],   # widgets: Topic B.push-type.widgets erlaubt
   "registration": "verify" | "approve",      # verify: Domain-Nachweis -> active, sonst pending; approve: immer pending
   "sandbox": true,                            # env "sandbox" erlaubt
   "public_host": "relay.example.org",         # @authority der Signatur (leer: Host-Kopf der Anfrage)
   "trust_proxy": true,                        # Client-IP aus X-Forwarded-For (letzter Eintrag, z. B. Traefik)
   "tiers": {"neu": {...}, "aktiv": {...}},    # ueberschreibt einzelne Werte der Standard-Stufen
   "reg_per_ip_hour": 5, "apns_concurrency": 32, "ring_slots": 4, "slot_wait": 2.0, "apns_timeout": 10,
   "operator": "Betreiber, Kontakt", "privacy_note": "zusaetzlicher Text fuer die Startseite"}
RELAY_DB (Standard /data/relay.db), RELAY_APNS_URL (nur Tests: Basis-URL statt Apple, http erlaubt),
RELAY_VERIFY_TEST_BASE (nur Tests: Domain-Nachweis ueber http://127.0.0.1:<port> mit Host-Kopf)."""
import json
import os
import re

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

PUSH_TYPES = ("alert", "background", "voip", "liveactivity", "widgets")
APNS_HOSTS = {"production": "https://api.push.apple.com", "sandbox": "https://api.sandbox.push.apple.com"}
MAX_BODY = 8192                     # Anfragekoerper ans Relais
MAX_PAYLOAD, MAX_PAYLOAD_VOIP = 4096, 5120  # APNs-Koerper (Apple-Grenzen)
BUNDLE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{1,199}")
TEAM_RE = re.compile(r"[A-Z0-9]{10}")

# Stufen (2.4): Werte je Minute; "day" = Tagesdeckel. ring hat eigenen Topf und reservierte APNs-Plaetze.
DEFAULT_TIERS = {
    "neu": {"ring": {"per_min": 30, "per_token_min": 10}, "alert": {"per_min": 120, "per_token_min": 30, "per_day": 5000},
            "bg": {"per_min": 60, "per_token_min": 6}},
    "aktiv": {"ring": {"per_min": 120, "per_token_min": 10}, "alert": {"per_min": 1200, "per_token_min": 30, "per_day": 100000},
              "bg": {"per_min": 600, "per_token_min": 6}},
}
TIER_KEYS = ("per_min", "per_token_min", "per_day")
RATES = ("ring", "alert", "bg")
STAGES = ("neu", "aktiv", "eigen")
PROMOTE_AFTER = 7 * 86400           # neu -> aktiv nach 7 Tagen ohne Sperre


class ConfigError(Exception):
    pass


def merge_tier(base, over):
    """Stufe base mit Teilwerten over ueberschreiben (nur bekannte Raten/Schluessel, nur Zahlen >= 0)."""
    out = {r: dict(v) for r, v in base.items()}
    for r, vals in (over or {}).items():
        if r not in RATES or not isinstance(vals, dict):
            raise ConfigError(f"Stufe: unbekannte Rate {r!r}")
        for k, v in vals.items():
            if k not in TIER_KEYS or isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                raise ConfigError(f"Stufe: ungueltiger Wert {r}.{k}")
            out.setdefault(r, {})[k] = v
    return out


def _load_p8(path):
    with open(path, "rb") as f:
        key = serialization.load_pem_private_key(f.read().strip(), None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ConfigError(f"{path}: kein P-256-Schluessel (.p8 von Apple)")
    return key


class Config:
    def __init__(self, data, base_dir="."):
        if not isinstance(data, dict):
            raise ConfigError("relay.json: Objekt erwartet")
        self.teams = []
        self.topic_team = {}        # Bundle -> Team
        self.widgets = set()        # Bundles mit Topic B.push-type.widgets
        for t in data.get("teams") or []:
            tid, kid = str(t.get("team_id") or ""), str(t.get("key_id") or "")
            if not TEAM_RE.fullmatch(tid) or not TEAM_RE.fullmatch(kid):
                raise ConfigError("Team: team_id und key_id je 10 Zeichen A-Z/0-9")
            p8 = os.path.join(base_dir, str(t.get("p8_file") or ""))
            team = {"team_id": tid, "key_id": kid, "key": _load_p8(p8), "bundles": []}
            for b in t.get("bundles") or []:
                b = str(b)
                if not BUNDLE_RE.fullmatch(b) or b in self.topic_team:
                    raise ConfigError(f"Bundle ungueltig oder doppelt: {b!r}")
                team["bundles"].append(b)
                self.topic_team[b] = team
                if t.get("widgets"):
                    self.widgets.add(b)
            self.teams.append(team)
        if not self.topic_team:
            raise ConfigError("relay.json: mindestens ein Team mit Bundle noetig")
        self.registration = data.get("registration", "verify")
        if self.registration not in ("verify", "approve"):
            raise ConfigError("registration: verify oder approve")
        self.sandbox = bool(data.get("sandbox", True))
        self.public_host = str(data.get("public_host") or "").strip().lower()
        self.trust_proxy = bool(data.get("trust_proxy", False))
        tiers = data.get("tiers") or {}
        self.tiers = {s: merge_tier(DEFAULT_TIERS[s], tiers.get(s)) for s in DEFAULT_TIERS}
        self.reg_per_ip_hour = int(data.get("reg_per_ip_hour", 5))
        self.apns_concurrency = max(int(data.get("apns_concurrency", 32)), 2)
        self.ring_slots = min(max(int(data.get("ring_slots", 4)), 1), self.apns_concurrency - 1)
        self.slot_wait = float(data.get("slot_wait", 2.0))
        self.apns_timeout = float(data.get("apns_timeout", 10))
        self.operator = str(data.get("operator") or "")[:300]
        self.privacy_note = str(data.get("privacy_note") or "")[:2000]
        self.apns_url = os.environ.get("RELAY_APNS_URL", "").rstrip("/")       # nur Tests
        self.verify_test_base = os.environ.get("RELAY_VERIFY_TEST_BASE", "").rstrip("/")   # nur Tests

    def push_types(self):
        return list(PUSH_TYPES)

    def bundles(self):
        return sorted(self.topic_team)

    def bundle_for(self, push_type, topic):
        """Topic-Regel je Typ -> Bundle B oder None (fremdes Topic)."""
        if push_type in ("alert", "background"):
            return topic if topic in self.topic_team else None
        sfx = {"voip": ".voip", "liveactivity": ".push-type.liveactivity", "widgets": ".push-type.widgets"}.get(push_type)
        b = topic[:-len(sfx)] if sfx and topic.endswith(sfx) else None
        if push_type == "widgets" and b not in self.widgets:
            return None
        return b if b in self.topic_team else None

    def limits(self, stage, own=None):
        """Wirksame Grenzen: neu/aktiv aus der Konfiguration, eigen = aktiv + Werte des Servers."""
        if stage == "eigen":
            try:
                return merge_tier(self.tiers["aktiv"], own or {})
            except ConfigError:
                return self.tiers["aktiv"]
        return self.tiers.get(stage) or self.tiers["neu"]

    def apns_base(self, env):
        return self.apns_url or APNS_HOSTS[env]


def load(path=None):
    path = path or os.environ.get("RELAY_CONFIG", "/data/relay.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return Config(data, os.path.dirname(os.path.abspath(path)))
