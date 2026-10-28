"""Foederation (2.0): Seiten zwischen Saga-Instanzen teilen - eigenes, schlankes Protokoll ueber den normalen Web-Port.

Grundsaetze (Einzelheiten: docs/ANLEITUNG.md, Abschnitt Foederation):
- Vertrauen nur zwischen ausdruecklich verbundenen Instanzen (Einladungscode + beidseitige Bestaetigung mit Fingerprints).
  Jede Server-zu-Server-Anfrage und jede Antwort ist signiert (RFC 9421, Ed25519, saga.fedsig): Uhrzeit +-5 min, Nonce
  gegen Wiederholung, Content-Digest, Host des Empfaengers in der Signatur.
- Daten bleiben beim Eigentuemer: der Heimatserver der lesenden Person ruft im Namen dieser Person ab (Proxy). Die Rolle
  prueft immer der Eigentuemer-Server (sharing.Access mit dem Prinzipal '@name@host'); der Heimatserver bestaetigt Person
  und Gruppen signiert (Saga-Actor).
- Beim Heimatserver haben fremde Seiten und Kommentare negative IDs: -(id_dort * 1024 + peer_id). Eigene Routen (Konverter
  'fedid') fangen genau diese ab - Editor, Kommentare und Seitenleiste arbeiten unveraendert ueber /api/notes/{id}.
- Ausgehende Verbindungen nur ueber netguard (DNS einmal aufgeloest, IP gepinnt; interne Adressen nur, wenn der Host unter
  "Freigegebene interne Hosts" steht), nur https (http nur mit FED_ALLOW_HTTP=1 fuer Tests), ohne Weiterleitungen.
- Keine Inhalte in Logs; Pruefprotokoll (fed_log) nur mit Kennungen."""
import base64
import hashlib
import json
import os
import re
import secrets
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.convertors import Convertor, register_url_convertor

import auth
from db import clean_line, connect as _db
from saga import avatars, bus, comments, fed_dbx, fedsig, i18n, netguard, notify, pages, people, sharing
from saga.core import BASE_URL, E, HANDLE_RE, RESTORE_TEST, SECRET, STATE_DIR, _api_guard, _bad, _body, _session_user, local_ref, parse_json, resolve_ref, t, title
from saga.files import _thumb, _thumb_mime, _thumb_w
from saga.notes import BLOCK_ID_RE, FILE_MAX, FILES_DIR, _disposition, _iter_blocks
from saga.publish import NAME_RE, get_name
from saga.sharing import ROLES, is_remote, remote_label
from version import VERSION

router = APIRouter()

PROTOCOL = 2                                 # 2.3: Protokoll 2 (Faehigkeiten, Express, Ketten); Protokoll 1 bleibt gueltig
PROTOCOLS = [1, 2]
ALLOW_HTTP = E.get("FED_ALLOW_HTTP", "").lower() in ("1", "true", "yes", "on")
SELF_URL = BASE_URL.rstrip("/")
SELF_HOST = fedsig.authority(urlparse(SELF_URL).netloc)
REF = 1024                                   # negative ID = -(id_dort * REF + peer_id)
PEER_MAX = REF - 1
REMOTE_ID_MAX = 2 ** 43                      # JS-sichere Zahlen nach der Umrechnung
RATE = max(60, int(E.get("FED_RATE", "600")))          # Anfragen je Minute und verbundener Instanz
EV_RATE = 120                                # Ereignis-Sendungen (/fed/v1|v2/events, je bis EVENTS_MAX) je Minute und Instanz
WF_RATE = 120                                # WebFinger-Abfragen (Personensuche) je Minute und Instanz
PEER_SLOTS = max(1, int(E.get("FED_PEER_SLOTS", "3")))  # gleichzeitige Hintergrund-Auftraege je Instanz im Express-Pool
TIMEOUT = float(E.get("FED_TIMEOUT", "10"))
LIST_TIMEOUT = 4.0                           # "Mit mir geteilt": je Instanz hoechstens so lange warten
RESP_MAX = 4 * 1024 * 1024
INVITE_TTL = 86400
OUTBOX_KEEP = 3 * 86400
EVENTS_MAX = 20
DISPLAY_MAX = 120
STATUSES = ("pending_in", "pending_out", "active", "disconnected")
CAPS = ["share", "read", "edit", "comment", "files", "notify"]
# Protokoll 2 (2.3): v2 = /fed/v2/hello + /fed/v2/events (Ereignis-Kennung, Ketten), express = synchrone Zustellung,
# handles = pseudonyme Handles ('~xxxx') wie oeffentliche Namen, directory = Adressbuch-Abgleich, keys = Schluessel-
# verzeichnis fuer saga-chat/v2, chat2 = foederierte Chats (saga.fed_chat), calls2 = Anrufe ueber Servergrenzen
# (saga.fed_calls). E4-E6 (saga.fed_pages): live = Live-Ereignisse fuer Abos fremder Seiten, presence = Anwesenheit,
# db = Datenbanken (Zeilen, Ansichten, Werte), subpages = Unterseiten anlegen/verschieben, upload = Bilder/Dateien
# hochladen, versions = Versionen, export = Export; chatgroups = freie Gruppen und Seiten-Chats ueber Servergrenzen;
# nc_files = Nextcloud-Dateien in Seiten (saga.nextcloud): Vorschau/Download nur fuer Bloecke mit Schalter "fed".
# calls3 (2.4) = Anrufe als Raum (saga.fed_calls): weitere eigene Geraete (Begleitgeraet, Uebernahme) und Gruppenanrufe
# als Mesh ueber Servergrenzen (Auftraege join/leave/roster/invite).
# chatfile2 (2.4): grosse Chat-Anhaenge (saga-file/v2, Videos) bereichsweise abrufen (GET /fed/v2/chat/files/{id}?range=a-b,
# signiert je Teil) - ohne chatfile2 gehen nur Anhaenge bis FILE_MAX (wie 2.3).
# chat3 (2.5): Reaktionen, Nachricht bearbeiten, Sprachnachrichten (saga.chat_x3: Auftraege react/edit, Ereignisse
# chat.react/chat.msg.edit) - nur, wenn alle Server eines Chats es koennen (Chat-Feld x3_ok).
# 2.4 (saga.chat_status): receipts = Gelesen-Bestaetigungen in Chats (versiegelt, ueber das Heim des Chats), online =
# Online-Status von Chat-Partnern (Abo beim Server der Person). Mit 2.3-Servern: nichts davon, nichts wird gesendet.
CAPS2 = ["v2", "express", "handles", "directory", "keys", "chat2", "calls2", "live", "presence", "db", "subpages", "upload", "versions",
         "export", "chatgroups", "nc_files", "calls3", "chatfile2", "receipts", "online", "rename", "tplbtn",
         "widgets1", "rowtpl", "csv", "formula1", "inline2", "cols6", "dbx1", "widgets2", "merge1", "presence2", "ment1", "sync1"]
# merge1 (2.5, P3 Stufe 1, saga.merge) = blockweises Zusammenfuehren beim Bearbeiten einer fremden Seite: das Heim reicht
# merge/base/bases des Clients im PATCH an den Eigentuemer weiter, der fuehrt zusammen (wie lokal) und antwortet mit
# merged + Inhalt/Titel (maskiert wie GET fuer den Prinzipal). Konfliktbloecke tragen nur Prinzipal/Handle und Anzeigename.
# Ohne merge1: das Heim laesst die Felder weg -> 409 wie bisher.
# presence2 (2.5, P3) = Anwesenheit je Block: POST /fed/v2/notes/{id}/presence nimmt block, Personen in Antwort und
# Live-Eintrag presence tragen block und color (Farbe bestimmt der Eigentuemer, aus dem Hash der Kennung). Ohne presence2:
# Anwesenheit wie bisher (ohne Block).
# ment1 (2.5, P6-5, saga.mentions) = @Erwaehnungen: der Eigentuemer liefert span.hk-mention mit Prinzipal ('@name@host',
# lokale Konten mit oeffentlichem Namen/Handle) samt people-Woerterbuch; Mitteilungen page.mention/comment.mention als
# Ereignis. Ohne ment1: Erwaehnungen als reiner Text (Speichern zurueck: pages.legacy_keep erhaelt sie), keine Mitteilung.
# sync1 (2.5, P6-6, saga.synced) = synchronisierte Bloecke: Kopien kommen mit eingesetztem Inhalt und data.src (Rechte des
# Prinzipals), bearbeiteter Inhalt einer Kopie wird beim Eigentuemer ins Original geschrieben. Ohne sync1: Block als
# Rueckfall-Karte, eingereichte Inhalte werden ignoriert (Block bleibt unveraendert).
# dbx1 (2.5, Paket 1, saga.fed_dbx) = Personen- und Datei-Eigenschaften: der Eigentuemer liefert Personen als Prinzipal
# ('@name@host', lokale Konten mit oeffentlichem Namen/Handle) samt people-Woerterbuch, Dateien als /files/<id> (das Heim
# holt sie ueber /fed/v1/files mit note=<zeile>); GET /fed/v2/notes/{db}/people = Auswahl; Mitteilung db.assigned als
# Ereignis. Ohne dbx1: Personen-/Datei-Spalten als Text-Spalte ohne Werte (fx_orig), "unsupported": ["person", "files"].
# widgets2 (2.5, Paket 4) = Widgets Etappe 2 + Diagramme: Wetter, Gewohnheiten (inkl. Abhaken ueber
# POST /fed/v2/notes/{seite}/widgets/{block}/habit), Diagramm-Widget und Diagramm-Ansicht (GET /fed/v2/notes/{db}/chart) rechnet
# der Eigentuemer-Server mit dem Prinzipal der Person; W traegt "data". "Anstehendes" ist persoenlich (Kalender/Aufgaben des
# Heim-Servers) und kommt ueber Servergrenzen immer als no_access. Ohne widgets2: diese Arten fehlen in der Antwort.
# csv (2.5, Paket 2) = Datenbank als CSV exportieren (GET /fed/v2/notes/{id}/export.csv, saga.csv_export) mit den Rechten
# der fremden Person. Ohne csv: Menuepunkt fehlt fuer Datenbanken dieses Servers.
# formula1 (2.5, Paket 1, saga.dbcalc) = Formel- und Rollup-Eigenschaften: der Eigentuemer-Server rechnet die Werte mit
# dem Prinzipal der Person und liefert sie fertig in props (dazu fx_err je Zeile, rtype im Schema, Gruppen mit value);
# POST /fed/v2/notes/{db}/formula/check prueft eine Formel fuer den Editor. Ohne formula1 (aeltere Gegenstelle): Formel/
# Rollup als nur-lesbare Text-Spalte (Original in fx_orig, kommt beim Speichern des Schemas unveraendert zurueck).
# inline2, cols6 (2.5, P6-0, docs/APP-API.md "Inline-Vertrag") = der Server kennt Inline-Farben (span.hk-c-*, mark.hk-bg-*),
# Erwaehnungen (span.hk-mention data-u), Inline-Mathe (code.hk-math) bzw. bis 6 Spalten. Speichert ein Peer OHNE diese
# Faehigkeit eine Seite hier, erhaelt der Eigentuemer-Server Marken und Spalten 5-6 aus dem alten Stand (pages.legacy_keep).
# rowtpl (2.5, fix27 E1/E4) = Eintrags-Vorlagen fuer Bearbeitende: GET .../rows liefert ab Rolle 'Bearbeiten' "templates",
# POST /fed/v2/notes/{vorlage}/duplicate geht dann auch ohne via (Vorlage liegt in parent_id), die Antwort traegt
# fresh/placeholder, und POST /fed/v2/notes/{id}/discard verwirft einen unveraenderten neuen Eintrag. Ohne rowtpl: wie bisher.
# widgets1 (2.5, saga.page_widgets) = Widgets in Seiten: Werte (Fortschritt/Kennzahl/Zitat aus einer Datenbank) einer
# foederiert geteilten Seite rechnet der Eigentuemer-Server mit dem Prinzipal der Person (GET /fed/v2/notes/{id}/widgets).
# Ohne widgets1: das Heim antwortet {"widgets": {}, "unsupported": true}.
# tplbtn (2.5) = Schaltflaeche "Neuer Eintrag" mit Vorlage auf einer foederiert geteilten Seite: Ziel und Vorlage der
# Schaltflaechen kommen als negative IDs (saga.federation._db_blocks), das Heim legt den Eintrag ueber
# POST /fed/v2/notes/{vorlage}/duplicate (parent_id, via) beim Eigentuemer an (saga.fed_pages). Ohne tplbtn: Hinweis.
# rename (2.5, Bughunt W02/W03): Person mit stabilem Handle - beim Anfragen nennt der Server der Person zusaetzlich ihren
# Handle (actor.handle); aendert sie ihren oeffentlichen Namen, kommt person.renamed (alt -> neu, Handle) statt person.gone.
# Die Gegenseite schreibt Freigaben, Kommentare, Personen usw. auf den neuen Namen um. Ohne rename: wie bisher (person.gone).
# horn (2.5, Gjallarhorn, docs/HORN-E2E.md) = Gruppenanrufe ueber Knoten mit Frame-E2E: Signale fkey/fkey-ack laufen
# versiegelt wie alle Anruf-Signale (Auftrag signal). Nur gemeldet, solange das Feature-Flag calls.horn an ist (caps2()).
# horn_dm (2.5) = 1:1 aus foederierten Direkt-Chats ueber Knoten (Auftrag dmhorn, saga/fed_calls.py) - gemeldet, solange
# horn an ist; ob ein Anruf so laeuft, entscheidet der Server der anrufenden Person (calls.horn_all).
# chat3 (2.5, saga/chat_x3.py) = Reaktionen/Bearbeiten ueber Servergrenzen - nur mit der Einstellung chat_v3 (Standard aus);
# aus: nicht gemeldet, eingehende x3-Auftraege 403 bzw. Ereignisse "ignored" (F.feature ist dann fuer alle Instanzen falsch).
CAPS2_FLAG = {"horn": lambda: bool((auth.settings().get("calls") or {}).get("horn")),   # wie saga.calls.horn_on()
              "horn_dm": lambda: bool((auth.settings().get("calls") or {}).get("horn")),
              "chat3": lambda: bool(auth.settings().get("chat_v3"))}                    # wie saga.chat.v3_new()


def caps2():
    """Eigene Faehigkeiten (Protokoll 2) - die festen und die, deren Feature-Flag gerade an ist."""
    return CAPS2 + [k for k, on in CAPS2_FLAG.items() if on()]


# Admin-Schalter je verbundener Instanz (fed_peer.opts) - Standard: alles an, Ausnahme "Teilen nur in gemeinsamen
# Gruppen" aus. directory_groups: nur Personen dieser Gruppen erscheinen im Adressbuch der Instanz (leer = alle).
OPT_DEFAULTS = {"chat": True, "calls": True, "directory": True, "scope_exception": False, "directory_groups": []}
# Faehigkeit -> Admin-Schalter, der sie zusaetzlich braucht
CAP_OPT = {"directory": "directory", "keys": "chat", "chat2": "chat", "calls2": "calls", "chatgroups": "chat", "calls3": "calls",
           "chatfile2": "chat", "receipts": "chat", "online": "chat", "chat3": "chat", "horn": "calls", "horn_dm": "calls"}
EXPRESS_TIMEOUT = float(E.get("FED_EXPRESS_TIMEOUT", "5"))      # synchrone Zustellung: harte Frist in Sekunden
HELLO_EVERY = 6 * 3600                       # Faehigkeiten der Gegenstelle so oft neu abfragen
SEEN_KEEP = 7 * 86400                        # Ereignis-Kennungen so lange merken (idempotent)
PSEUDO_RE = re.compile(r"~[a-z2-7]{16}")      # pseudonymer Handle (stabil, zufaellig, nie der Anmeldename)
_CHAIN_RE = re.compile(r"[A-Za-z0-9:._-]{1,80}")
_EVID_RE = re.compile(r"[0-9a-f]{32}")
_FID = re.compile(r"[0-9a-f]{24}")
_HOST_RE = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*(:[0-9]{1,5})?")


class FedError(Exception):
    """Fehler bei der Verbindung zu einer anderen Instanz. code: HTTP-Status der Gegenseite (oder None)."""

    def __init__(self, msg, code=None, retry_after=None):
        super().__init__(msg)
        self.code = code
        self.retry_after = retry_after           # HTTP 429: Wartezeit der Gegenseite in Sekunden (Retry-After, begrenzt)


def enabled():
    return sharing.fed_enabled()


# =====================================================================================================
# Kleinkram: Adressen, Verweise, Protokoll, Raten
# =====================================================================================================
def norm_base(url):
    """Basis-Adresse einer Instanz -> 'https://host[:port]' (klein, ohne Pfad) oder None."""
    u = urlparse(str(url or "").strip())
    if u.scheme not in (("https", "http") if ALLOW_HTTP else ("https",)) or u.username or u.password or u.query or u.fragment:
        return None
    if u.path not in ("", "/"):
        return None
    host = fedsig.authority(u.netloc)
    if not _HOST_RE.fullmatch(host) or len(host) > 253:
        return None
    return f"{u.scheme}://{host}"


def host_of(base):
    return fedsig.authority(urlparse(base).netloc)


def ref(peer_id, rid):
    return -(int(rid) * REF + int(peer_id))


def unref(x):
    """negative ID -> (peer_id, id_dort)"""
    x = -int(x)
    return x % REF, x // REF


def _ok_rid(v):
    return isinstance(v, int) and not isinstance(v, bool) and 0 < v < REMOTE_ID_MAX


# Trennpunkte, mit denen Saga die Herkunft anzeigt ("Anna Berg · b.example") - in Namen anderer Server ersetzt, damit
# niemand eine fremde Herkunft vortaeuschen kann ("Bert · b.example" als Name auf c.example)
_SEP = re.compile("[\u00b7\u2022\u2219\u22c5\u30fb\uff65\u2027\u0387\u16eb\u2e31\u2e33]")


def remote_name(s, n=None):
    """Anzeigename von einer anderen Instanz: bereinigt (_clean_text), ohne Trennpunkte."""
    return re.sub(r"\s{2,}", " ", _SEP.sub(" ", _clean_text(s, n or DISPLAY_MAX))).strip()


def _clean_text(s, n):
    """Text der Gegenstelle: nur Zeichenketten/Zahlen, ohne Steuer-, Bidi- und unsichtbare Zeichen (BUG-038)."""
    if isinstance(s, bool) or not isinstance(s, (str, int, float)):
        return ""
    return clean_line(s).strip()[:n]


_log_seen = {}


def log(c, event, peer=None, detail="", host=None, throttle=None):
    """Pruefprotokoll. throttle: Schluessel - hoechstens ein Eintrag je Minute (abgewiesene Signaturen)."""
    if throttle is not None:
        now = time.time()
        if now - _log_seen.get(throttle, 0) < 60:
            return
        if len(_log_seen) > 5000:
            _log_seen.clear()
        _log_seen[throttle] = now
    c.execute("INSERT INTO fed_log (peer_id, host, ts, event, detail) VALUES (?,?,?,?,?)",
              (peer["id"] if peer else None, (peer["host"] if peer else host) or "", time.time(), event, str(detail)[:300]))


class _Rate:
    def __init__(self):
        self.hits, self.lock = {}, threading.Lock()

    def allow(self, key, limit, window=60.0):
        now = time.monotonic()
        with self.lock:
            start, n = self.hits.get(key, (now, 0))
            if now - start >= window:
                start, n = now, 0
            if len(self.hits) > 5000:
                self.hits.clear()
            self.hits[key] = (start, n + 1)
            return n + 1 <= limit

    def wait(self, key, window=60.0):
        """Sekunden bis zum naechsten Zeitfenster (fuer Retry-After)."""
        with self.lock:
            start, _ = self.hits.get(key, (time.monotonic(), 0))
        return max(1, int(window - (time.monotonic() - start)) + 1)


_rate = _Rate()


def retry_after(v):
    """Retry-After der Gegenseite (Sekunden) -> 1..900 oder None. Datumsangaben und Unsinn: None (eigener Backoff)."""
    try:
        n = int(str(v or "").strip())
    except ValueError:
        return None
    return max(1, min(900, n)) if n >= 0 else None


# =====================================================================================================
# Eigener Schluessel
# =====================================================================================================
_key = {}


def _fernet():
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("saga-fed-key|" + SECRET).encode()).digest()))


def own_key():
    """-> (privat_roh, oeffentlich_roh). Beim ersten Aufruf erzeugt (verschluesselt gespeichert)."""
    if "k" in _key:
        return _key["k"]
    with _db() as c, c:
        r = c.execute("SELECT public_key, private_key FROM fed_self WHERE id=1").fetchone()
        if not r:
            priv, pub = fedsig.new_key()
            c.execute("INSERT OR IGNORE INTO fed_self (id, public_key, private_key, created) VALUES (1,?,?,?)",
                      (fedsig.b64u(pub), fedsig.seal_key(_fernet(), priv), time.time()))
            r = c.execute("SELECT public_key, private_key FROM fed_self WHERE id=1").fetchone()
    try:
        priv = fedsig.open_key(_fernet(), r[1])
    except InvalidToken:
        raise FedError("Instanz-Schluessel nicht lesbar (SECRET_KEY geaendert?)")
    pub = fedsig.unb64u(r[0])
    if fedsig.public_of(priv) != pub:
        raise FedError("Instanz-Schluessel beschaedigt")
    _key["k"] = (priv, pub)
    return _key["k"]


def own_fingerprint():
    return fedsig.fingerprint(own_key()[1])


def has_key(c):
    return bool(c.execute("SELECT 1 FROM fed_self WHERE id=1").fetchone())


# =====================================================================================================
# Gegenstellen (Peers)
# =====================================================================================================
P_COLS = ("id, host, base_url, name, public_key, fingerprint, status, created, confirmed_at, last_seen, last_error, protocol, caps, caps_at, "
          "opts, dir_seq, dir_cfg, dir_at")
_P_NAMES = ("id", "host", "base_url", "name", "public_key", "fingerprint", "status", "created", "confirmed_at", "last_seen", "last_error",
            "protocol", "caps", "caps_at", "opts", "dir_seq", "dir_cfg", "dir_at")


def _jl(v, typ):
    try:
        x = json.loads(v or "")
    except ValueError:
        return typ()
    return x if isinstance(x, typ) else typ()


def _peer(r):
    if not r:
        return None
    p = dict(zip(_P_NAMES, r))
    p["caps"] = [x for x in _jl(p["caps"], list) if isinstance(x, str)]
    p["opts"] = peer_opts(p["opts"])
    return p


def peer_opts(raw):
    """Admin-Schalter einer Instanz (fed_peer.opts) mit Standardwerten."""
    d = raw if isinstance(raw, dict) else _jl(raw, dict)
    out = dict(OPT_DEFAULTS)
    for k, v in d.items():
        if k == "directory_groups":
            out[k] = sorted({auth.norm_group(g) for g in v if isinstance(g, str) and auth.norm_group(g)})[:50] if isinstance(v, list) else []
        elif k in OPT_DEFAULTS:
            out[k] = bool(v)
    return out


def caps_of(p):
    """Gemeinsame Faehigkeiten (beide Seiten melden sie) - ohne Admin-Schalter."""
    return set(caps2()) & set(p.get("caps") or []) if p else set()


def feature(p, name):
    """Darf Funktion name mit Instanz p genutzt werden? Beide Seiten melden die Faehigkeit, die Verbindung ist aktiv,
    die Foederation an und der zugehoerige Admin-Schalter je Instanz an (Aushandlung, Protokoll 2)."""
    if not p or p.get("status") != "active" or not enabled() or name not in caps_of(p):
        return False
    opt = CAP_OPT.get(name)
    return bool(peer_opts(p.get("opts")).get(opt, True)) if opt else True


def scope_ok(host=None):
    """"Teilen nur in gemeinsamen Gruppen" (Admin) sperrt die Foederation beim Teilen - ausser fuer Instanzen mit der
    Ausnahme (Schalter je Instanz). host None: gibt es ueberhaupt eine Instanz, mit der es geht?"""
    if not sharing.group_scope():
        return True
    with _db() as c:
        ps = [peer_by(c, "host", host)] if host else peers(c)
    return any(p and p["status"] == "active" and p["opts"].get("scope_exception") for p in ps)


def peer_by(c, col, v):
    assert col in ("id", "host", "base_url")
    return _peer(c.execute(f"SELECT {P_COLS} FROM fed_peer WHERE {col}=?", (v,)).fetchone())


def peers(c):
    return [_peer(r) for r in c.execute(f"SELECT {P_COLS} FROM fed_peer ORDER BY host").fetchall()]


def _free_id(c):
    used = {r[0] for r in c.execute("SELECT id FROM fed_peer")}
    for i in range(1, PEER_MAX + 1):
        if i not in used:
            return i
    return None


def _person_upsert(c, principal, peer_id, display):
    display = remote_name(display)
    r = c.execute("SELECT display FROM fed_person WHERE principal=?", (principal,)).fetchone()
    if r is None or r[0] != display:
        c.execute("INSERT OR REPLACE INTO fed_person (principal, peer_id, display, updated) VALUES (?,?,?,?)",
                  (principal, peer_id, display, time.time()))


def is_name(n):
    """Name einer Person ueber Instanzgrenzen: oeffentlicher Name oder pseudonymer Handle ('~' + 16 Zeichen)."""
    return isinstance(n, str) and bool(NAME_RE.fullmatch(n) or PSEUDO_RE.fullmatch(n))


_A32 = "abcdefghijklmnopqrstuvwxyz234567"


def own_handle(c, uid):
    """Stabiler pseudonymer Handle eines lokalen Kontos ('~' + 16 Zeichen, zufaellig; beim ersten Bedarf angelegt).
    Er aendert sich nie (auch nicht mit dem oeffentlichen Namen) - an ihn binden sich die Chat-Schluessel (saga-chat/v2)."""
    r = c.execute("SELECT handle FROM fed_handle WHERE uid=?", (uid,)).fetchone()
    if r:
        return r[0]
    for _ in range(5):
        h = "~" + "".join(secrets.choice(_A32) for _ in range(16))
        c.execute("INSERT INTO fed_handle (uid, handle, created) VALUES (?,?,?) ON CONFLICT DO NOTHING", (uid, h, time.time()))
        r = c.execute("SELECT handle FROM fed_handle WHERE uid=?", (uid,)).fetchone()
        if r:
            return r[0]
    raise FedError("handle")


def uid_by_handle(c, h):
    r = c.execute("SELECT f.uid FROM fed_handle f JOIN users u ON u.username=f.uid WHERE f.handle=? AND u.disabled=0", (h,)).fetchone()
    return r[0] if r else None


def safe_display(disp, uid, email=""):
    """Anzeigename, wenn er weder der Anmeldename noch die E-Mail-Adresse ist (SSO/LDAP liefern als Namen manchmal die
    Adresse oder die Kennung) - sonst ''."""
    d = _clean_text(disp or "", DISPLAY_MAX)
    low = d.lower()
    if not d or low == str(uid).lower() or (email and low == str(email).strip().lower()):
        return ""
    return d


def display_of(c, uid):
    """Anzeigename fuer andere Instanzen: Anzeigename (nie der Anmeldename/die E-Mail), sonst oeffentlicher Name, sonst ''."""
    r = c.execute("SELECT display_name, email FROM users WHERE username=?", (uid,)).fetchone()
    return (safe_display(r[0], uid, r[1]) if r else "") or get_name(c, uid) or ""


def fed_name(c, uid, p=None, pseudo=True):
    """Name eines lokalen Kontos fuer die Instanz p: oeffentlicher Name, ohne ihn (2.3) der pseudonyme Handle - nur, wenn
    p Protokoll 2 mit 'handles' spricht (aeltere Instanzen: weiter nur mit oeffentlichem Namen). pseudo=False: nur der
    oeffentliche Name."""
    pub = get_name(c, uid)
    if pub:
        return pub
    if pseudo and p is not None and "handles" in caps_of(p) and p.get("status") == "active":
        return own_handle(c, uid)
    return None


def _host_tail(host):
    return "@" + host


# Wo Personen anderer Instanzen ('@name@host') hier noch vorkommen - solange, bleibt ihr Anzeigename (fed_person) gespeichert
_PERSON_REFS = (("note_comment", "uid"), ("note_comment", "resolved_by"), ("note", "updated_by"), ("note", "created_by"),
                ("note_version", "author"), ("notification", "actor_uid"), ("note_share", "target"), ("fed_inbound", "owner"),
                ("chat_member", "uid"), ("chat_msg", "sender_uid"), ("file", "fed_by"))


def prune_persons(c, peer_id=None, principals=None):
    """Anzeigenamen fremder Personen entfernen, die hier nirgends mehr vorkommen (nach dem Trennen bzw. wenn eine Person
    nicht mehr im Adressbuch ihres Servers stehen will). Wer in Kommentaren, Versionen, Chats o. ae. steht, behaelt den
    Namen (sonst stuenden dort nur Kennungen). -> Anzahl entfernter."""
    if principals is not None:
        cand = [p for p in principals if isinstance(p, str) and p.startswith("@")]
    else:
        cand = [r[0] for r in c.execute("SELECT principal FROM fed_person WHERE peer_id=?", (peer_id,)).fetchall()]
    n = 0
    for pr in cand:
        if any(c.execute(f"SELECT 1 FROM {tb} WHERE {col}=? LIMIT 1", (pr,)).fetchone() for tb, col in _PERSON_REFS):
            continue
        if c.execute("SELECT 1 FROM fed_chat_block WHERE target=? LIMIT 1", (pr[1:],)).fetchone():
            continue                         # Sperrliste zeigt den Namen ('~x@host' ohne fuehrendes @)
        n += c.execute("DELETE FROM fed_person WHERE principal=?", (pr,)).rowcount
    return n


def cleanup_peer(c, p):
    """Verbindung getrennt: Freigaben an Personen/Gruppen dieser Instanz, Eingangs-Verweise, Gelesen-Marken und offene
    Ereignisse weg; Anzeigenamen nur noch, wo sie hier vorkommen (alte Kommentare, Chat-Verlauf)."""
    tail = _host_tail(p["host"])
    rows = c.execute("SELECT note_id, owner_uid, target_type, target FROM note_share WHERE target_type IN ('remote','remote_group') "
                     "AND right(target, ?)=?", (len(tail), tail)).fetchall()
    for nid, owner, typ, tg in rows:
        c.execute("DELETE FROM note_share WHERE note_id=? AND target_type=? AND target=?", (nid, typ, tg))
    c.execute("DELETE FROM fed_inbound WHERE peer_id=?", (p["id"],))
    c.execute("DELETE FROM note_comment_seen WHERE uid LIKE '@%' AND right(uid, ?)=?", (len(tail), tail))
    c.execute("DELETE FROM fed_outbox WHERE peer_id=?", (p["id"],))
    avatars.forget_remote(c, host=p["host"])
    # Protokoll 2: Adressbuch der Instanz sofort weg, Schluessel-Zwischenspeicher, Ketten- und Ereignis-Stand
    for tb in ("fed_directory", "fed_chat_keys", "fed_chain_in", "fed_chain_out", "fed_event_seen", "fed_alias"):
        c.execute(f"DELETE FROM {tb} WHERE peer_id=?", (p["id"],))
    c.execute("UPDATE fed_peer SET dir_seq=0, dir_cfg='', dir_at=NULL WHERE id=?", (p["id"],))
    for fn in list(PEER_GONE_HOOKS):         # 2.3: foederierte Chats schreibgeschuetzt, Anrufe enden (saga.fed_chat, saga.fed_calls)
        fn(c, p)
    prune_persons(c, peer_id=p["id"])
    return len(rows)


# Spalten mit Prinzipalen fremder Personen, die beim Umbenennen umgeschrieben werden (Seiten-Bereich; Chats und Anrufe
# fuehren fremde Personen ohnehin unter dem stabilen Handle '@~x@host' - die bleiben unberuehrt)
_REKEY_COLS = (("note_comment", "uid"), ("note_comment", "resolved_by"), ("note", "updated_by"), ("note", "created_by"),
               ("note_version", "author"), ("notification", "actor_uid"), ("file", "fed_by"))


def _rekey(c, peer, old, new, handle):
    """Person der Instanz peer heisst jetzt new statt old (beides '@name@host' derselben Instanz): Freigaben, Eingangs-
    Verweise, Kommentare, Versionen, Benachrichtigungen, Anzeigename, Adressbuch und Zwischenspeicher umschreiben.
    Gibt es fuer new schon eine Freigabe derselben Seite, gilt die hoehere Rolle. -> Anzahl umgeschriebener Freigaben."""
    tail = _host_tail(peer["host"])
    if old == new or not old.endswith(tail) or not new.endswith(tail):
        return 0
    n = 0
    # Kam old hier nie vor (kein Anzeigename, keine Freigabe, kein Eingangs-Verweis - _person_upsert merkt sich jede
    # Person, die anfragt, kommentiert oder Ziel ist), bleibt es bei Adressbuch und Zwischenspeichern
    known = c.execute("SELECT 1 FROM fed_person WHERE principal=? UNION ALL SELECT 1 FROM note_share WHERE target_type='remote' AND "
                      "target=? UNION ALL SELECT 1 FROM fed_inbound WHERE peer_id=? AND owner=? LIMIT 1", (old, old, peer["id"], old)).fetchone()
    for sid, nid, role in (c.execute("SELECT id, note_id, role FROM note_share WHERE target_type='remote' AND target=?", (old,)).fetchall()
                           if known else ()):
        r = c.execute("SELECT id, role FROM note_share WHERE note_id=? AND target_type='remote' AND target=?", (nid, new)).fetchone()
        if r:
            if sharing.RANK.get(role, 0) > sharing.RANK.get(r[1], 0):
                c.execute("UPDATE note_share SET role=? WHERE id=?", (role, r[0]))
            c.execute("DELETE FROM note_share WHERE id=?", (sid,))
        else:
            c.execute("UPDATE note_share SET target=? WHERE id=?", (new, sid))
        n += 1
    if known:
        for tb, col in _REKEY_COLS:
            c.execute(f"UPDATE {tb} SET {col}=? WHERE {col}=?", (new, old))
    from saga.people import rename_principal       # 2.5: Personen-Werte in Datenbank-Zeilen (row_person)
    rename_principal(c, old, new)
    from saga import mentions                      # 2.5 (P6-5): Erwaehnungen in Seiten (Inhalt + note_mention)
    mentions.rename_principal(c, old, new)
    if known:
        c.execute("UPDATE note_comment_seen SET uid=? WHERE uid=? AND NOT EXISTS (SELECT 1 FROM note_comment_seen s2 WHERE s2.uid=? "
                  "AND s2.thread_id=note_comment_seen.thread_id)", (new, old, new))
        c.execute("DELETE FROM note_comment_seen WHERE uid=?", (old,))
        c.execute("UPDATE fed_inbound SET owner=? WHERE peer_id=? AND owner=?", (new, peer["id"], old))
        r = c.execute("SELECT display FROM fed_person WHERE principal=?", (old,)).fetchone()
        if r and not c.execute("SELECT 1 FROM fed_person WHERE principal=?", (new,)).fetchone():
            _person_upsert(c, new, peer["id"], r[0])
        avatars.forget_remote(c, principal=old)
    c.execute("DELETE FROM fed_name_handle WHERE principal IN (?,?)", (old, new))
    name = new[1:-len(tail)]
    if NAME_RE.fullmatch(name):              # oeffentlicher Name -> Handle (Seiten-Chats, saga.fed_chat)
        c.execute("INSERT INTO fed_name_handle (principal, peer_id, handle, name, fetched) VALUES (?,?,?,'',?)",
                  (new, peer["id"], handle, time.time()))
    c.execute("UPDATE fed_directory SET pub=? WHERE peer_id=? AND handle=?", (name if NAME_RE.fullmatch(name) else "", peer["id"], handle))
    if known:
        prune_persons(c, principals=[old])
    if n:
        bus.emit_after(c, "shared.changed", {"owner": None, "note_id": None, "target_type": "remote", "target": new})
    return n


def _alias_set(c, peer, handle, principal):
    c.execute("INSERT INTO fed_alias (peer_id, handle, principal, updated) VALUES (?,?,?,?) ON CONFLICT (peer_id, handle) DO UPDATE "
              "SET principal=EXCLUDED.principal, updated=EXCLUDED.updated", (peer["id"], handle, principal, time.time()))


def _alias_seen(c, peer, handle, principal):
    """Anfrage im Namen einer Person, deren Server ihren stabilen Handle mitnennt (Faehigkeit rename): Handle und aktueller
    Name sind dieselbe Person. Freigaben an den Handle (W02) bzw. an einen frueheren Namen (W03, Ereignis verpasst) gehen
    auf den aktuellen Namen ueber. Nur Personen der anfragenden Instanz selbst (principal endet auf deren Host)."""
    r = c.execute("SELECT principal FROM fed_alias WHERE peer_id=? AND handle=?", (peer["id"], handle)).fetchone()
    if r and r[0] == principal:
        return
    if c.execute("SELECT 1 FROM fed_alias WHERE peer_id=? AND principal=? AND handle<>?", (peer["id"], principal, handle)).fetchone():
        log(c, "person.conflict", peer, f"{principal[1:]} {handle}", throttle=(peer["id"], "alias", handle))
        return                               # Name gehoert hier schon zu einer anderen Person dieser Instanz: nichts uebernehmen
    olds = [r[0]] if r else []
    hp = f"@{handle}{_host_tail(peer['host'])}"
    if hp != principal and hp not in olds:
        olds.append(hp)
    n = sum(_rekey(c, peer, o, principal, handle) for o in olds)
    _alias_set(c, peer, handle, principal)
    if n or r:
        log(c, "person.alias", peer, f"{', '.join(o[1:] for o in olds)} -> {principal[1:]} shares={n}")


def alias_principal(c, peer, handle):
    """Bekannter aktueller Prinzipal der Person mit diesem Handle auf peer (oder None)."""
    r = c.execute("SELECT principal FROM fed_alias WHERE peer_id=? AND handle=?", (peer["id"], handle)).fetchone()
    return r[0] if r else None


# =====================================================================================================
# Ausgehende Verbindungen (netguard, signiert)
# =====================================================================================================
def _allowed_hosts():
    return {h.strip().lower() for h in re.split(r"[,\s]+", auth.settings().get("allowed_hosts") or "") if h.strip()}


def _ip_ok(ip, host, port):
    """Oeffentliche Adressen immer; interne nur mit Freigabe im Admin (wie Kalender/Immich)."""
    if netguard.is_public(ip):
        return True
    if auth.settings().get("allow_private_hosts"):
        return True
    hs = _allowed_hosts()
    return f"{host}:{port}".lower() in hs or str(host).lower() in hs


def _client(timeout=TIMEOUT):
    return netguard.guarded_client(_ip_ok, timeout=timeout, follow_redirects=False)


def _send(method, url, headers, body, limit, timeout=TIMEOUT):
    """-> (status, kopfzeilen (klein), bytes). Bricht bei mehr als limit Bytes ab. FedError bei Netzfehlern."""
    # Zeitlimit von httpx gilt je Lesevorgang - eine Gegenseite, die Byte fuer Byte traeufelt, haette sonst unbegrenzt
    # einen Arbeits-Thread. Daher zusaetzlich eine Gesamtfrist (Dateien bekommen mehr Zeit).
    if RESTORE_TEST:                                      # Testwiederherstellung: nie nach aussen (saga/backups.py)
        raise FedError("SAGA_RESTORE_TEST")
    deadline = time.monotonic() + (timeout * 3 if limit <= RESP_MAX else max(60.0, timeout * 6))
    try:
        with _client(timeout) as cl:
            with cl.stream(method, url, headers=headers, content=body) as r:
                buf = bytearray()
                for chunk in r.iter_bytes():
                    buf += chunk
                    if len(buf) > limit:
                        raise FedError("Antwort zu gross")
                    if time.monotonic() > deadline:
                        raise FedError("Zeitueberschreitung")
                return r.status_code, {k.lower(): v for k, v in r.headers.items()}, bytes(buf)
    except httpx.TimeoutException:
        raise FedError("Zeitueberschreitung")
    except (httpx.HTTPError, netguard.Blocked) as ex:
        if isinstance(ex, netguard.Blocked) or isinstance(ex.__cause__, netguard.Blocked) or "nicht freigegeben" in str(ex):
            raise FedError("Adresse nicht erlaubt (interne Adresse – im Admin unter „Freigegebene interne Hosts“ eintragen)")
        raise FedError(f"nicht erreichbar ({netguard.net_why(ex)})")


def fetch_info(base):
    """Oeffentliche Angaben einer Instanz (/.well-known/saga) -> dict mit Schluessel und Fingerprint. FedError."""
    status, _, data = _send("GET", base + "/.well-known/saga", {"Accept": "application/json", "User-Agent": f"Saga/{VERSION}"}, None, 65536)
    if status != 200:
        raise FedError(f"keine Saga-Instanz mit Foederation (HTTP {status})", status)
    try:
        d = parse_json(data)
        pub = fedsig.load_public(d["key"]["public"])
    except (ValueError, KeyError, TypeError, fedsig.SigError):
        raise FedError("ungueltige Angaben der Instanz")
    if d.get("software") != "saga" or norm_base(d.get("base_url")) != base:
        raise FedError("Instanz meldet eine andere Adresse")
    return {"base_url": base, "host": host_of(base), "name": _clean_text(d.get("name"), 80), "version": _clean_text(d.get("version"), 40),
            "public": fedsig.b64u(pub), "fingerprint": fedsig.fingerprint(pub)}


def call(peer, method, path, query="", body=None, actor=None, limit=RESP_MAX, timeout=TIMEOUT):
    """Signierte Anfrage an eine verbundene Instanz -> (status, kopfzeilen, bytes). Die Antwort muss mit dem gepinnten
    Schluessel signiert sein (sonst FedError). query: schon kodiert ('a=1&b=2')."""
    priv, _ = own_key()
    raw = b"" if body is None else (body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode())
    hdr, nonce = fedsig.sign_request(priv, SELF_URL, method, urlparse(peer["base_url"]).netloc, path, query, raw, actor)
    # Sprache der anfragenden Person (Platzhalter wie "Seite ohne Zugriff", Fehlertexte) - unsigniert, nur Darstellung
    hdr.update({"Content-Type": "application/json", "Accept": "application/json", "User-Agent": f"Saga/{VERSION}",
                "Accept-Language": i18n.get_lang() or "en"})
    status, rh, data = _send(method, peer["base_url"] + path + ("?" + query if query else ""), hdr, raw, limit, timeout)
    try:
        fedsig.verify_response(fedsig.unb64u(peer["public_key"]), peer["base_url"], status, rh, data, nonce)
    except fedsig.SigError as ex:
        # (aeltere Instanzen antworten auf "zu viele Anfragen" unsigniert - der Status zaehlt trotzdem fuer den Backoff)
        raise FedError(f"Antwort nicht signiert/ungueltig ({ex}; HTTP {status})", status,
                       retry_after(rh.get("retry-after")) if status == 429 else None)
    return status, rh, data


def call_json(peer, method, path, query="", body=None, actor=None, timeout=TIMEOUT):
    status, _, data = call(peer, method, path, query, body, actor, timeout=timeout)
    try:
        d = parse_json(data) if data else {}         # Surrogate/NUL der Gegenseite bereinigt (BUG-008)
    except (ValueError, RecursionError):
        raise FedError("ungueltige Antwort", status)
    if not pages._json_depth_ok(d, 32):             # Antworten sind flach; tief Verschachteltes nur von boesartiger Gegenseite
        raise FedError("ungueltige Antwort", status)
    return status, d


def _touch(c, peer, err=None):
    now = time.time()
    if err is not None:
        c.execute("UPDATE fed_peer SET last_error=? WHERE id=?", (str(err)[:300], peer["id"]))
    elif not peer.get("last_seen") or now - peer["last_seen"] > 60 or peer.get("last_error"):
        c.execute("UPDATE fed_peer SET last_seen=?, last_error=NULL WHERE id=?", (now, peer["id"]))
        if peer.get("last_error"):           # wieder erreichbar: Wartendes gleich zustellen statt beim naechsten Termin (BUG-089)
            if c.execute("UPDATE fed_outbox SET next_try=0 WHERE peer_id=? AND next_try>?", (peer["id"], now)).rowcount:
                c.after_commit(_wake.set)


# =====================================================================================================
# Eingehende Anfragen pruefen
# =====================================================================================================
class _In:
    """Gepruefte Anfrage einer verbundenen Instanz."""
    __slots__ = ("peer", "nonce", "body", "data", "principal", "name", "groups")


def _unauth(reason="unauthorized", code=401):
    return JSONResponse({"error": reason}, status_code=code, headers={"Cache-Control": "no-store"})


def _not_found():
    return JSONResponse({"error": "not found"}, status_code=404, headers={"Cache-Control": "no-store"})


def _raw_target(request):
    path = (request.scope.get("raw_path") or request.url.path.encode()).decode("latin-1")
    return path, (request.scope.get("query_string") or b"").decode("latin-1")


def _verify_sync(method, path, query, headers, body, statuses, connect_key=None, connect_base=None, raw=False):
    """-> (_In, None) oder (None, fehlerantwort). Laeuft im Thread (Datenbank)."""
    holder = {}

    def lookup(keyid):
        if connect_key is not None:                    # Verbindungsanfrage: Schluessel kommt im Koerper mit
            if keyid != connect_base:
                raise fedsig.SigError("keyid")
            return connect_key
        with _db() as c:
            p = peer_by(c, "base_url", keyid)
        if not p or p["status"] not in statuses:
            raise fedsig.SigError("unknown peer")
        holder["peer"] = p
        return fedsig.unb64u(p["public_key"])

    try:
        keyid, nonce, actor = fedsig.verify_request(lookup, method, SELF_HOST, path, query, headers, body)
    except fedsig.SigError as ex:
        with _db() as c, c:
            p = holder.get("peer")
            log(c, "sig.rejected", p, str(ex), host=None if p else _clean_text(headers.get("signature-input", "")[-80:], 80),
                throttle=(p["id"] if p else "?", str(ex)))
        return None, _unauth()
    peer = holder.get("peer") or {"id": None, "host": host_of(keyid), "base_url": keyid}
    if not _rate.allow(peer["base_url"], RATE):
        # signiert (die Gegenstelle erkennt eine echte Bremse) und mit Wartezeit - sie wartet je Instanz, statt einzeln zu probieren
        priv, _ = own_key()
        body_ = json.dumps({"error": "too many requests"}).encode()
        h = fedsig.sign_response(priv, SELF_URL, 429, body_, nonce)
        h.update({"Cache-Control": "no-store", "Retry-After": str(_rate.wait(peer["base_url"]))})
        return None, Response(body_, status_code=429, media_type="application/json", headers=h)
    inp = _In()
    inp.peer, inp.nonce, inp.body, inp.data = peer, nonce, body, None
    inp.principal, inp.name, inp.groups = None, None, set()
    with _db() as c, c:
        if c.execute("INSERT OR IGNORE INTO fed_nonce (peer, nonce, created) VALUES (?,?,?)", (keyid, nonce, time.time())).rowcount == 0:
            log(c, "sig.replay", peer if peer.get("id") else None, "", host=peer["host"], throttle=(keyid, "replay"))
            return None, _unauth("replay")
        if actor is not None:
            name = actor.get("name")
            groups = actor.get("groups") or []
            if not is_name(name) or not isinstance(groups, list) or len(groups) > 50 \
                    or any(not isinstance(g, str) or auth.norm_group(g) != g for g in groups) or peer.get("id") is None:
                return None, _unauth("bad actor", 400)
            inp.name = name
            inp.principal = f"@{name}@{peer['host']}"
            inp.groups = {f"{g}@{peer['host']}" for g in groups}
            _person_upsert(c, inp.principal, peer["id"], actor.get("display") or name)
            h = actor.get("handle")          # 2.5 (rename): stabiler Handle der Person - Handle und Name sind dieselbe Person
            if isinstance(h, str) and PSEUDO_RE.fullmatch(h) and (name == h or NAME_RE.fullmatch(name)):
                _alias_seen(c, peer, h, inp.principal)
        if peer.get("id"):
            _touch(c, peer)
    if body and not raw:
        try:
            inp.data = parse_json(body)              # Surrogate/NUL der Gegenseite bereinigt (BUG-008)
        except (ValueError, RecursionError):
            return None, _unauth("bad json", 400)
    return inp, None


async def _verify(request, statuses=("active",), need_actor=False, raw=False):
    """Signatur einer verbundenen Instanz pruefen -> (_In, None) oder (None, antwort). raw: Koerper sind Bytes (Datei)."""
    if not enabled():
        return None, _not_found()
    body = await request.body()
    path, query = _raw_target(request)
    inp, err = await run_in_threadpool(_verify_sync, request.method, path, query, request.headers, body, statuses, None, None, raw)
    if err is None and need_actor and inp.principal is None:
        return None, _unauth("actor required", 400)
    return inp, err


def _signed(inp, status, data, media="application/json", extra=None):
    """Signierte Antwort (Content-Digest, Saga-Nonce = Nonce der Anfrage)."""
    body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
    priv, _ = own_key()
    h = fedsig.sign_response(priv, SELF_URL, status, body, inp.nonce)
    h.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
    h.update(extra or {})
    return Response(body, status_code=status, media_type=media, headers=h)


def _as(inp, fn, *args):
    """fn(prinzipal, *args) mit den signiert bestaetigten Gruppen der fremden Person (im Thread)."""
    tok = sharing.REMOTE_GROUPS.set({inp.principal: frozenset(inp.groups)})
    try:
        return fn(inp.principal, *args)
    except pages._Reply as r:
        return r.resp
    finally:
        sharing.REMOTE_GROUPS.reset(tok)


PERSON_KEYS = frozenset(("name", "username", "me", "remote", "avatar", "handle"))


def _is_person(obj):
    """Personen-Objekt (sharing.person & Co.): name, username, me - dazu hoechstens remote/avatar/handle."""
    return {"name", "username", "me"} <= set(obj) and set(obj) <= PERSON_KEYS


def _ext_persons(c, obj, cache=None, peer=None):
    """Antwort an eine andere Instanz: Personen nur mit oeffentlichem Namen ('@name@host') - nie der Anmeldename.
    Fremde Personen behalten ihre Adresse, der Name kommt ohne Herkunfts-Zusatz (setzt der Empfaenger)."""
    cache = {} if cache is None else cache
    if isinstance(obj, list):
        return [_ext_persons(c, x, cache, peer) for x in obj]
    if not isinstance(obj, dict):
        return obj
    if _is_person(obj):                      # Profilbild-Adressen gelten nur hier - die andere Instanz setzt eigene
        u = obj.get("username")
        if not u:
            return {"name": obj.get("name"), "username": None, "me": False}
        if u not in cache:
            if is_remote(u):
                r = c.execute("SELECT display FROM fed_person WHERE principal=?", (u,)).fetchone()
                cache[u] = {"name": (r[0] if r else "") or u[1:].split("@")[0], "username": u}
            else:
                pub = fed_name(c, u, peer)          # oeffentlicher Name bzw. (Protokoll 2) pseudonymer Handle
                cache[u] = {"name": display_of(c, u) or pub or "?", "username": f"@{pub}@{SELF_HOST}" if pub else None}
        return dict(cache[u], me=bool(obj.get("me")))
    return {k: _ext_persons(c, v, cache, peer) for k, v in obj.items()}


def _relay(inp, resp):
    """Antwort eines lokalen Endpunkts (JSONResponse) signiert an die anfragende Instanz - Personen umgeschrieben."""
    try:
        data = json.loads(resp.body or b"null")
    except ValueError:
        data = None
    with _db() as c:
        data = _ext_persons(c, data, peer=inp.peer)
        if isinstance(data, dict) and resp.status_code == 200:
            data = _ext_content(c, data, inp.peer)
    return _signed(inp, resp.status_code, data)


def _ext_content(c, d, peer):
    """2.5 (P6-5/P6-6): Inhalt einer Seite fuer eine andere Instanz - Erwaehnungen als Prinzipal (ment1) bzw. Text, das
    people-Woerterbuch der Erwaehnungen mit Prinzipalen; synchronisierte Bloecke ohne sync1 als Rueckfall (ohne Inhalt).
    Kommentare: Token "<@~handle>" -> "<@@name@hier>" bzw. "@Name"."""
    from saga import mentions
    ment = feature(peer, "ment1")
    dl = d.get("delta") if isinstance(d.get("delta"), dict) and isinstance(d["delta"].get("blocks"), list) else None
    if dl is not None and "body" not in d:           # 2.5 (merge1 ?since): nur geaenderte Bloecke - gleich behandeln
        d["body"], d["format"] = json.dumps({"blocks": dl["blocks"]}, ensure_ascii=False), "blocks"
    if isinstance(d.get("body"), str) and d.get("format") == "blocks":
        body = d["body"]
        refs = list(mentions.refs_in(body))
        if refs:
            pm = d.get("people") if isinstance(d.get("people"), dict) else {}
            body = mentions.out_body(c, body, peer, ment)
            outref = {}
            for r in refs:
                if HANDLE_RE.fullmatch(r):
                    u = resolve_ref(c, r)
                    pub = fed_name(c, u, peer) if u else None
                    outref[r] = f"@{pub}@{SELF_HOST}" if pub else None
                else:
                    outref[r] = r
            if ment:
                d["people"] = dict({k: v for k, v in pm.items() if k not in outref},
                                   **{outref[r]: {"name": (pm.get(r) or {}).get("name") or "?", "gone": bool((pm.get(r) or {}).get("gone"))}
                                      for r in refs if outref.get(r)})
                for r in refs:               # lokale Handles nie nach aussen (auch nicht als Schluessel)
                    if HANDLE_RE.fullmatch(r):
                        d["people"].pop(r, None)
            elif isinstance(d.get("people"), dict):
                for r in refs:
                    d["people"].pop(r, None)
        if '"synced"' in body and not feature(peer, "sync1"):
            try:
                doc = json.loads(body)
                for b in _iter_blocks(doc.get("blocks") or []):
                    if b.get("type") == "synced" and isinstance(b.get("data"), dict):
                        b["data"] = {"sid": b["data"].get("sid")}
                body = json.dumps(doc, ensure_ascii=False)
            except (ValueError, RecursionError):
                pass
        d["body"] = body
    if dl is not None and isinstance(d.get("body"), str) and d.get("format") == "blocks" and "delta" in d:
        try:
            dl["blocks"] = json.loads(d.pop("body")).get("blocks") or []
        except ValueError:
            d.pop("body", None)
    if isinstance(d.get("threads"), list):           # Kommentare (GET .../comments)
        for th in d["threads"]:
            for cm in th.get("comments") or [] if isinstance(th, dict) else []:
                if isinstance(cm, dict) and isinstance(cm.get("body"), str) and "<@" in cm["body"]:
                    cm["body"] = _ext_tokens(c, cm["body"], peer, ment)
        pm = d.pop("people", None)
        if ment and isinstance(pm, dict):
            out = {}
            for k, v in pm.items():
                if not isinstance(v, dict):
                    continue
                if HANDLE_RE.fullmatch(k):
                    u = resolve_ref(c, k)
                    pub = fed_name(c, u, peer) if u else None
                    if not pub:
                        continue
                    k = f"@{pub}@{SELF_HOST}"
                out[k] = {"name": v.get("name"), "gone": bool(v.get("gone"))}
            d["people"] = out
    if isinstance(d.get("comment"), dict) and isinstance(d["comment"].get("body"), str) and "<@" in d["comment"]["body"]:
        d["comment"]["body"] = _ext_tokens(c, d["comment"]["body"], peer, ment)
    return d


def _ext_tokens(c, body, peer, ment):
    from saga import mentions

    def rep(m):
        r = m.group(1)
        if HANDLE_RE.fullmatch(r):
            u = resolve_ref(c, r)
            pub = fed_name(c, u, peer) if u else None
            if ment and pub:
                return f"<@@{pub}@{SELF_HOST}>"
            return "@" + (mentions.people_map(c, [r], None).get(r) or {}).get("name", "?")
        if ment:
            return m.group(0)
        return "@" + (mentions.people_map(c, [r], None).get(r) or {}).get("name", "?")
    return mentions._TOKEN.sub(rep, body)


# =====================================================================================================
# Server-zu-Server: Kennung, Nachschlagen, Verbinden
# =====================================================================================================
@router.get("/.well-known/saga")
def well_known():
    """Oeffentliche Angaben dieser Instanz (nur bei eingeschalteter Foederation)."""
    if not enabled():
        return _not_found()
    try:
        _, pub = own_key()
    except FedError:
        return _not_found()
    return JSONResponse({"software": "saga", "protocol": PROTOCOL, "protocols": PROTOCOLS, "version": VERSION, "base_url": SELF_URL,
                         "host": SELF_HOST, "name": title(), "key": {"id": SELF_URL, "alg": "ed25519", "public": fedsig.b64u(pub)},
                         "fingerprint": fedsig.fingerprint(pub), "capabilities": CAPS + caps2()},
                        headers={"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"})


def _local_user_by_name(c, name):
    if isinstance(name, str) and PSEUDO_RE.fullmatch(name):          # pseudonymer Handle (Protokoll 2)
        uid = uid_by_handle(c, name)
        return (uid, display_of(c, uid) or name) if uid else (None, None)
    r = c.execute("SELECT p.uid FROM public_name p JOIN users u ON u.username=p.uid WHERE p.name=? AND u.disabled=0",
                  (name,)).fetchone()
    return (r[0], display_of(c, r[0]) or name) if r else (None, None)


def _renamed_uid(c, name):
    """Frueherer oeffentlicher Name eines (noch aktiven) Kontos - Ereignisse, die die Gegenseite vor person.renamed
    abgeschickt hat, erreichen die Person trotzdem (nur Ereignisse; Nachschlagen/Teilen geht nur mit dem aktuellen Namen)."""
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        return None
    r = c.execute("SELECT r.uid FROM fed_name_retired r JOIN users u ON u.username=r.uid WHERE r.name=? AND u.disabled=0", (name,)).fetchone()
    return r[0] if r else None


def _local_group(c, name):
    return bool(c.execute("SELECT 1 FROM groups WHERE name=? UNION SELECT 1 FROM user_ext_group WHERE name=? LIMIT 1", (name, name)).fetchone())


@router.get("/.well-known/webfinger")
async def webfinger(request: Request, resource: str = "", kind: str = "user"):
    """Person (oeffentlicher Name) oder Gruppe nachschlagen - NUR fuer verbundene Instanzen (signiert), sonst 404."""
    if not request.headers.get("signature"):
        return _not_found()
    inp, err = await _verify(request)
    if err is not None:
        return err
    if not _rate.allow("wf:" + inp.peer["base_url"], WF_RATE):      # Personensuche: Flut gebremst (Namen durchprobieren)
        return _signed(inp, 429, {"error": "too many requests"}, extra={"Retry-After": str(_rate.wait("wf:" + inp.peer["base_url"]))})
    m = re.fullmatch(r"acct:([^@\s]{1,100})@([^@\s]{1,260})", resource or "")
    if not m or fedsig.authority(m.group(2)) != SELF_HOST:
        return _signed(inp, 404, {"error": "not found"})

    def run():
        with _db() as c:
            if kind == "group":
                g = auth.norm_group(m.group(1))
                if g and g == m.group(1) and _local_group(c, g):
                    return {"subject": f"acct:{g}@{SELF_HOST}", "kind": "group", "name": g}
                return None
            if not is_name(m.group(1)):
                return None
            uid, disp = _local_user_by_name(c, m.group(1))
            # pseudonymer Handle: Anzeigename nur, wenn das Konto im Adressbuch dieser Instanz steht (Opt-out je Konto);
            # ein bewusst gesetzter oeffentlicher Name bleibt wie bisher mit Anzeigenamen auffindbar
            if uid and PSEUDO_RE.fullmatch(m.group(1)) and not (DIR_VISIBLE is not None and DIR_VISIBLE(c, uid, inp.peer)):
                disp = m.group(1)
            return {"subject": f"acct:{m.group(1)}@{SELF_HOST}", "kind": "user", "name": disp} if uid else None
    d = await run_in_threadpool(run)
    return _signed(inp, 200 if d else 404, d or {"error": "not found"})


@router.post("/fed/v1/connect")
async def fed_connect(request: Request):
    """Verbindungsanfrage einer anderen Instanz mit Einladungscode. Der Schluessel kommt im Koerper (Besitznachweis ueber
    die Signatur); zusaetzlich holen wir /.well-known/saga der anfragenden Adresse - der Schluessel muss dort derselbe sein."""
    if not enabled():
        return _not_found()
    ip = request.client.host if request.client else "?"
    if not _rate.allow("connect:" + ip, 20, 3600):
        return _unauth("too many requests", 429)
    body = await request.body()
    try:
        d = parse_json(body)
        base = norm_base(d.get("base_url"))
        key = fedsig.load_public(d.get("key"))
        code = str(d.get("code") or "")
    except (ValueError, TypeError, AttributeError, fedsig.SigError):
        return _unauth("bad request", 400)
    if not base or not code or len(code) > 100:
        return _unauth("bad request", 400)
    path, query = _raw_target(request)
    inp, err = await run_in_threadpool(_verify_sync, "POST", path, query, request.headers, body, (), key, base)
    if err is not None:
        return err
    status, out = await run_in_threadpool(_accept_connect, base, key, code, _clean_text(d.get("name"), 80))
    return _signed(inp, status, out)


def _accept_connect(base, key, code, name):
    host = host_of(base)
    if host == SELF_HOST:
        return 400, {"error": "self"}
    with _db() as c:
        inv = c.execute("SELECT id FROM fed_invite WHERE code_hash=? AND used_at IS NULL AND expires>?",
                        (hashlib.sha256(code.encode()).hexdigest(), time.time())).fetchone()
        old = peer_by(c, "host", host)
    if not inv:
        with _db() as c, c:
            log(c, "connect.bad_code", None, "", host=host, throttle=("code", host))
        return 403, {"error": "invalid code"}
    if old and old["status"] == "active":
        return 409, {"error": "already connected"}
    try:
        info = fetch_info(base)                 # bindet den Schluessel an die Adresse
    except FedError as ex:
        return 400, {"error": f"well-known: {ex}"}
    if fedsig.unb64u(info["public"]) != key:
        return 403, {"error": "key mismatch"}
    now = time.time()
    with _db() as c, c:
        if c.execute("UPDATE fed_invite SET used_at=? WHERE id=? AND used_at IS NULL", (now, inv[0])).rowcount == 0:
            return 403, {"error": "invalid code"}
        old = peer_by(c, "host", host)
        pk, fp = fedsig.b64u(key), fedsig.fingerprint(key)
        if old:
            if old["status"] == "active":
                return 409, {"error": "already connected"}
            c.execute("UPDATE fed_peer SET base_url=?, name=?, public_key=?, fingerprint=?, status='pending_in', created=?, confirmed_at=NULL, "
                      "last_error=NULL WHERE id=?", (base, name or info["name"], pk, fp, now, old["id"]))
            pid = old["id"]
        else:
            pid = _free_id(c)
            if pid is None:
                return 409, {"error": "too many peers"}
            c.execute("INSERT INTO fed_peer (id, host, base_url, name, public_key, fingerprint, status, created) VALUES (?,?,?,?,?,?,?,?)",
                      (pid, host, base, name or info["name"], pk, fp, "pending_in", now))
        c.execute("UPDATE fed_invite SET peer_id=? WHERE id=?", (pid, inv[0]))
        log(c, "connect.request", peer_by(c, "id", pid), fp)
    _, pub = own_key()
    return 200, {"status": "pending", "base_url": SELF_URL, "name": title(), "key": fedsig.b64u(pub)}


@router.post("/fed/v1/confirm")
async def fed_confirm(request: Request):
    """Die Gegenseite hat unsere Anfrage bestaetigt -> aktiv."""
    inp, err = await _verify(request, ("pending_out", "active"))
    if err is not None:
        return err

    def run():
        with _db() as c, c:
            if inp.peer["status"] != "active":
                c.execute("UPDATE fed_peer SET status='active', confirmed_at=?, last_error=NULL WHERE id=?", (time.time(), inp.peer["id"]))
                log(c, "peer.confirmed_remote", inp.peer)
                c.after_commit(lambda: peer_submit(inp.peer["id"], hello, inp.peer["id"]))      # Protokoll 2: Faehigkeiten austauschen
    await run_in_threadpool(run)
    return _signed(inp, 200, {"status": "active"})


@router.post("/fed/v1/disconnect")
async def fed_disconnect(request: Request):
    """Die Gegenseite hat getrennt oder abgelehnt -> sofort getrennt, Freigaben weg."""
    inp, err = await _verify(request, ("pending_in", "pending_out", "active"))
    if err is not None:
        return err

    def run():
        with _db() as c, c:
            c.execute("UPDATE fed_peer SET status='disconnected', last_error=NULL WHERE id=?", (inp.peer["id"],))
            n = cleanup_peer(c, inp.peer)
            log(c, "peer.disconnected_remote", inp.peer, f"shares={n}")
    await run_in_threadpool(run)
    return _signed(inp, 200, {"status": "disconnected"})


@router.get("/fed/v1/status")
async def fed_status(request: Request):
    """Wie sehen wir die anfragende Instanz? (Admin der Gegenseite: "Status pruefen")"""
    inp, err = await _verify(request, ("pending_in", "active"))
    if err is not None:
        return err
    return _signed(inp, 200, {"status": "active" if inp.peer["status"] == "active" else "pending"})


# =====================================================================================================
# Server-zu-Server: Ereignisse (Teilen, Kommentare, Personen)
# =====================================================================================================
def _handle(v):
    """'name@host' -> (name, host) oder (None, None)."""
    m = re.fullmatch(r"@?([a-z0-9][a-z0-9_-]{1,31}|~[a-z2-7]{16})@([a-z0-9.:-]{1,260})", str(v or ""))
    return (m.group(1), fedsig.authority(m.group(2))) if m else (None, None)


def _actor_of(c, peer, a):
    """Ausloeser eines Ereignisses -> lokaler Benutzername (eigene Person) bzw. Prinzipal '@name@host' oder None."""
    if not isinstance(a, dict):
        return None
    name, host = _handle(a.get("handle"))
    if not name:
        return None
    if host == SELF_HOST:
        # Eigene Person (hat ueber diese Instanz kommentiert): nur, wenn von dort ueberhaupt etwas mit ihr geteilt ist -
        # sonst koennte die Gegenstelle Benachrichtigungen im Namen beliebiger lokaler Konten erzeugen (BUG-035)
        lu = _local_user_by_name(c, name)[0]
        if not lu:
            return None
        gs = sorted(sharing.groups_of(c, lu))
        sql = "SELECT 1 FROM fed_inbound WHERE peer_id=? AND ((target_type='user' AND target=?)" + \
              (f" OR (target_type='group' AND target IN ({','.join('?' * len(gs))}))" if gs else "") + ") LIMIT 1"
        return lu if c.execute(sql, (peer["id"], lu, *gs)).fetchone() else None
    p = f"@{name}@{host}"
    # Anzeigename: nur die eigene Instanz der Person darf ihn aendern - eine andere verbundene Instanz kann ihn hoechstens
    # erstmals setzen (sonst koennte sie Personen dritter Instanzen hier umbenennen)
    if host == peer["host"] or not c.execute("SELECT 1 FROM fed_person WHERE principal=?", (p,)).fetchone():
        _person_upsert(c, p, peer["id"], a.get("display") or name)
    return p


def _ev(c, peer, ev):
    """Ein Ereignis einer verbundenen Instanz verarbeiten (in der Transaktion). -> 'ok' | 'ignored'"""
    typ = ev.get("type")
    to = ev.get("to") if isinstance(ev.get("to"), dict) else {}
    note = ev.get("note") if isinstance(ev.get("note"), dict) else {}
    rid = note.get("id")
    if typ == "person.gone":
        name = ev.get("name")
        if not is_name(name):
            return "ignored"
        p = f"@{name}@{peer['host']}"
        n = c.execute("DELETE FROM note_share WHERE target_type='remote' AND target=?", (p,)).rowcount
        c.execute("UPDATE note_comment SET uid=NULL WHERE uid=?", (p,))
        c.execute("UPDATE note_comment SET resolved_by=NULL WHERE resolved_by=?", (p,))
        c.execute("DELETE FROM note_comment_seen WHERE uid=?", (p,))
        c.execute("UPDATE note SET updated_by=NULL WHERE updated_by=?", (p,))
        c.execute("UPDATE note SET created_by=NULL WHERE created_by=?", (p,))
        c.execute("UPDATE note_version SET author=NULL WHERE author=?", (p,))
        c.execute("UPDATE notification SET actor_uid=NULL WHERE actor_uid=?", (p,))
        c.execute("DELETE FROM fed_person WHERE principal=?", (p,))
        avatars.forget_remote(c, principal=p)
        c.execute("DELETE FROM fed_directory WHERE peer_id=? AND (handle=? OR pub=?)", (peer["id"], name, name))
        c.execute("DELETE FROM fed_alias WHERE peer_id=? AND (handle=? OR principal=?)", (peer["id"], name, p))
        if PSEUDO_RE.fullmatch(name):
            c.execute("DELETE FROM fed_chat_keys WHERE handle=?", (f"{name}@{peer['host']}",))
        for fn in list(PERSON_GONE_HOOKS):
            fn(c, peer, name, deleted=bool(ev.get("deleted")))
        if ev.get("deleted"):
            c.execute("DELETE FROM fed_inbound WHERE peer_id=? AND owner=?", (peer["id"], p))
        log(c, "person.gone", peer, f"{name} shares={n}")
        return "ok"
    if not _ok_rid(rid):
        return "ignored"
    tt, tname = to.get("type"), to.get("name")
    if tt == "user":
        if not is_name(tname):
            return "ignored"
        target = _local_user_by_name(c, tname)[0] or _renamed_uid(c, tname)
        if not target:
            return "ignored"
    elif tt == "group" and typ in ("share.added", "share.role", "share.removed"):
        target = auth.norm_group(tname) if isinstance(tname, str) else None
        if not target or target != tname or not _local_group(c, target):
            return "ignored"
    else:
        return "ignored"
    title_ = _clean_text(note.get("title"), 300)
    fed = {"peer": peer["id"], "note": rid, "title": title_}
    if typ in ("share.added", "share.role"):
        role = ev.get("role")
        owner = ev.get("owner") if isinstance(ev.get("owner"), dict) else {}
        oname = owner.get("name")
        if role not in ROLES or not is_name(oname):
            return "ignored"
        op = f"@{oname}@{peer['host']}"
        _person_upsert(c, op, peer["id"], owner.get("display") or oname)
        old = c.execute("SELECT role, owner FROM fed_inbound WHERE peer_id=? AND note_id=? AND target_type=? AND target=?",
                        (peer["id"], rid, tt, target)).fetchone()
        if old and old[0] == role and old[1] == op:
            return "ok"                      # nichts geaendert: keine neue Benachrichtigung (Wiederholung/Flut)
        c.execute("INSERT OR REPLACE INTO fed_inbound (peer_id, note_id, owner, target_type, target, role, created) VALUES (?,?,?,?,?,?,?)",
                  (peer["id"], rid, op, tt, target, role, time.time()))
        who = [target] if tt == "user" else notify.group_members(c, target)
        for u in who:
            notify.create(c, u, typ, op, None, data={"role": role, "fed": fed})
        bus.emit_after(c, "shared.changed", {"owner": op, "note_id": None, "target_type": tt, "target": target})
        return "ok"
    if typ == "share.removed":
        c.execute("DELETE FROM fed_inbound WHERE peer_id=? AND note_id=? AND target_type=? AND target=?", (peer["id"], rid, tt, target))
        bus.emit_after(c, "shared.changed", {"owner": None, "note_id": None, "target_type": tt, "target": target})
        return "ok"
    if typ == "db.assigned" and tt == "user":       # 2.5 (dbx1): Zeile muss von dort mit der Person geteilt sein
        gs = sorted(sharing.groups_of(c, target))
        sql = "SELECT 1 FROM fed_inbound WHERE peer_id=? AND ((target_type='user' AND target=?)" + \
              (f" OR (target_type='group' AND target IN ({','.join('?' * len(gs))}))" if gs else "") + ") LIMIT 1"
        if not feature(peer, "dbx1") or not c.execute(sql, (peer["id"], target, *gs)).fetchone():
            return "ignored"
        if _ok_rid(note.get("db")):                  # Zeile einer geteilten Datenbank: Zugriff ueber die Datenbank (fed_render)
            fed["db"] = note["db"]
        notify.create(c, target, typ, _actor_of(c, peer, ev.get("actor")), None, data={"fed": fed}, bundle=f"fed:{peer['id']}:{rid}")
        return "ok"
    if typ in ("page.mention", "comment.mention") and tt == "user":     # 2.5 (P6-5, ment1): erwaehnt auf einer Seite dort
        gs = sorted(sharing.groups_of(c, target))
        sql = "SELECT 1 FROM fed_inbound WHERE peer_id=? AND ((target_type='user' AND target=?)" + \
              (f" OR (target_type='group' AND target IN ({','.join('?' * len(gs))}))" if gs else "") + ") LIMIT 1"
        if not feature(peer, "ment1") or not c.execute(sql, (peer["id"], target, *gs)).fetchone():
            return "ignored"
        if typ == "page.mention":
            block = ev.get("block") if isinstance(ev.get("block"), str) and re.fullmatch(r"[A-Za-z0-9_-]{0,40}", ev.get("block")) else ""
            if block:
                fed["block"] = block
            notify.create(c, target, typ, _actor_of(c, peer, ev.get("actor")), None, data={"fed": fed})
            return "ok"
    if typ in ("comment.new", "comment.reply", "comment.mention") and tt == "user":
        cm = ev.get("comment") if isinstance(ev.get("comment"), dict) else {}
        cid, thread = cm.get("id"), cm.get("thread")
        if not _ok_rid(cid) or (thread is not None and not _ok_rid(thread)):
            return "ignored"
        block = cm.get("block") if isinstance(cm.get("block"), str) and re.fullmatch(r"[A-Za-z0-9_-]{0,40}", cm.get("block")) else ""
        fed.update(comment=ref(peer["id"], cid), thread=ref(peer["id"], thread or cid), block=block)
        notify.create(c, target, typ, _actor_of(c, peer, ev.get("actor")), None, data={"fed": fed})
        return "ok"
    return "ignored"


def _ev_renamed(c, peer, ev):
    """2.5 (Faehigkeit rename): Person der sendenden Instanz hat ihren oeffentlichen Namen geaendert (bzw. erstmals einen
    gesetzt: alt = Handle). {old, new, handle} - nur Personen dieser Instanz (Prinzipale mit ihrem Host), alt/neu je der
    oeffentliche Name oder genau der Handle; neu darf hier nicht schon einer anderen Person derselben Instanz gehoeren."""
    old, new, h = ev.get("old"), ev.get("new"), ev.get("handle")
    if not (isinstance(h, str) and PSEUDO_RE.fullmatch(h)):
        return "ignored"
    if not all(isinstance(x, str) and (x == h or NAME_RE.fullmatch(x)) for x in (old, new)) or old == new:
        return "ignored"
    tail = _host_tail(peer["host"])
    po, pn = f"@{old}{tail}", f"@{new}{tail}"
    if c.execute("SELECT 1 FROM fed_alias WHERE peer_id=? AND principal IN (?,?) AND handle<>?", (peer["id"], po, pn, h)).fetchone():
        log(c, "person.conflict", peer, f"{old} -> {new} ({h})", throttle=(peer["id"], "alias", h))
        return "ignored"
    cur = alias_principal(c, peer, h)
    olds = [x for x in dict.fromkeys((po, cur, f"@{h}{tail}")) if x and x != pn]
    n = sum(_rekey(c, peer, o, pn, h) for o in olds)
    _alias_set(c, peer, h, pn)
    log(c, "person.renamed", peer, f"{old} -> {new} shares={n}")
    return "ok"


@router.post("/fed/v1/events")
async def fed_events(request: Request):
    inp, err = await _verify(request)
    if err is not None:
        return err
    evs = (inp.data or {}).get("events") if isinstance(inp.data, dict) else None
    if not isinstance(evs, list) or len(evs) > EVENTS_MAX:
        return _signed(inp, 400, {"error": "bad events"})
    if not _rate.allow("ev:" + inp.peer["base_url"], EV_RATE):
        return _signed(inp, 429, {"error": "too many events"}, extra={"Retry-After": str(_rate.wait("ev:" + inp.peer["base_url"]))})

    def run():
        out = []
        with _db() as c, c:
            for ev in evs:
                out.append(_ev(c, inp.peer, ev) if isinstance(ev, dict) else "ignored")
        return out
    return _signed(inp, 200, {"results": await run_in_threadpool(run)})


# =====================================================================================================
# Protokoll 2 (2.3): Faehigkeiten aushandeln, Ereignisse mit Kennung und Kette
# =====================================================================================================
def _clean_caps(v):
    return sorted({x for x in v if isinstance(x, str) and re.fullmatch(r"[a-z0-9_-]{1,32}", x)})[:64] if isinstance(v, list) else []


def _store_caps(pid, protocol, caps):
    """Faehigkeiten der Gegenstelle speichern; bei Aenderung HELLO_HOOKS (z. B. Adressbuch holen) im Hintergrund."""
    protocol = protocol if isinstance(protocol, int) and not isinstance(protocol, bool) and 1 <= protocol <= 99 else 1
    with _db() as c, c:
        old = peer_by(c, "id", pid)
        if not old:
            return
        c.execute("UPDATE fed_peer SET protocol=?, caps=?, caps_at=? WHERE id=?", (protocol, json.dumps(caps), time.time(), pid))
        changed = old["protocol"] != protocol or sorted(old["caps"]) != caps
        if changed:
            log(c, "peer.capabilities", old, f"protocol={protocol} " + " ".join(x for x in caps if x in caps2()))
            if old["protocol"] >= 2 and (protocol < 2 or "v2" not in caps) and "v2" in old["caps"]:
                # Rueckfall auf Protokoll 1 (aeltere Version zurueckgespielt - oder ein Versuch, Schutz abzuschalten): sichtbar im
                # Pruefprotokoll; Ereignisse, die Protokoll 2 brauchen, warten im Ausgangskorb statt still verloren zu gehen
                log(c, "peer.downgrade", old, f"protocol {old['protocol']} -> {protocol}")
    if changed:
        _run_hooks(pid)


def _run_hooks(pid):
    def run():
        for fn in list(HELLO_HOOKS):
            try:
                fn(pid)
            except Exception as ex:  # noqa: BLE001
                print(f"federation: hook {getattr(fn, '__name__', '?')}: {type(ex).__name__}: {ex}", flush=True)
    peer_submit(pid, run)


HELLO_EXTRA = []                         # 2.5 P10: fn(peer_id) -> dict, zusaetzliche Angaben im hello (saga/fed_horn.py)
HELLO_IN = []                            # 2.5 P10: fn(peer_id, daten) - Angaben der Gegenseite aus dem hello


def _hello_extra(pid):
    out = {}
    for fn in HELLO_EXTRA:
        try:
            out.update(fn(pid) or {})
        except Exception as ex:  # noqa: BLE001 - nur Zusatzangaben
            print(f"federation: hello-Zusatz: {type(ex).__name__}", flush=True)
    return out


def _hello_in(pid, d):
    for fn in HELLO_IN:
        try:
            fn(pid, d)
        except Exception as ex:  # noqa: BLE001
            print(f"federation: hello-Zusatz: {type(ex).__name__}", flush=True)


def hello(pid):
    """Faehigkeiten mit einer verbundenen Instanz austauschen (signiert, POST /fed/v2/hello). Aeltere Instanzen kennen
    den Endpunkt nicht (404) -> Protokoll 1 ohne Zusatzfaehigkeiten. -> Faehigkeiten der Gegenstelle oder None."""
    with _db() as c:
        p = peer_by(c, "id", pid)
    if not p or p["status"] != "active" or not enabled():
        return None
    try:
        status, d = call_json(p, "POST", "/fed/v2/hello", body={"protocol": PROTOCOL, "protocols": PROTOCOLS, "capabilities": CAPS + caps2(),
                                                                 **_hello_extra(pid)}, timeout=6)
    except FedError as ex:
        if ex.code == 404:                   # 2.1/2.2: unsignierte 404 - Protokoll 1
            _store_caps(pid, 1, [])
            return []
        with _db() as c, c:
            _touch(c, p, ex)
        return None
    if status == 404:
        _store_caps(pid, 1, [])
        return []
    if status != 200 or not isinstance(d, dict):
        return None
    caps = _clean_caps(d.get("capabilities"))
    _store_caps(pid, d.get("protocol"), caps)
    _hello_in(pid, d)
    return caps


@router.post("/fed/v2/hello")
async def fed_hello(request: Request):
    """Gegenstelle nennt Protokoll und Faehigkeiten; Antwort: unsere. Beide Seiten speichern, was die andere kann."""
    inp, err = await _verify(request, ("pending_out", "active"))
    if err is not None:
        return err
    d = inp.data if isinstance(inp.data, dict) else {}
    extra = {}
    if inp.peer.get("id") and inp.peer.get("status") == "active":
        await run_in_threadpool(_store_caps, inp.peer["id"], d.get("protocol"), _clean_caps(d.get("capabilities")))
        await run_in_threadpool(_hello_in, inp.peer["id"], d)
        extra = await run_in_threadpool(_hello_extra, inp.peer["id"])
    return _signed(inp, 200, {"protocol": PROTOCOL, "protocols": PROTOCOLS, "capabilities": CAPS + caps2(), **extra})


class _RetryEv(Exception):
    pass


def _ev2(c, peer, ev):
    """Ereignis ueber Protokoll 2. Gibt der Zuhoerer 'retry' zurueck (z. B. foederierter Chat: Luecke wird erst nachgeholt),
    zaehlt das Ereignis als nicht verarbeitet (Kennung und Kette zurueckgesetzt) - der Absender stellt es spaeter erneut zu."""
    sp = getattr(c, "savepoint", None)
    if sp is None:
        return _ev2_one(c, peer, ev)
    try:
        with sp():
            r = _ev2_one(c, peer, ev)
            if r == "retry":
                raise _RetryEv()
            return r
    except _RetryEv:
        return "retry"


def _ev2_one(c, peer, ev):
    """Ereignis ueber Protokoll 2: Kennung (hoechstens einmal), Kette (streng aufsteigend), dann wie v1 bzw. EV2_HANDLERS."""
    eid = ev.get("id")
    if eid is not None:
        if not isinstance(eid, str) or not _EVID_RE.fullmatch(eid):
            return "ignored"
        if c.execute("INSERT INTO fed_event_seen (peer_id, ev_id, ts) VALUES (?,?,?) ON CONFLICT DO NOTHING",
                     (peer["id"], eid, time.time())).rowcount == 0:
            return "dup"
    chain, cseq = ev.get("chain"), ev.get("cseq")
    if chain is not None:
        if not isinstance(chain, str) or not _CHAIN_RE.fullmatch(chain) or not isinstance(cseq, int) or isinstance(cseq, bool) \
                or not 0 < cseq < 2 ** 62:
            return "ignored"
        cep = ev.get("cep") if isinstance(ev.get("cep"), str) and re.fullmatch(r"[0-9a-f]{8,32}", ev.get("cep")) else ""
        r = c.execute("SELECT seq, epoch FROM fed_chain_in WHERE peer_id=? AND chain=?", (peer["id"], chain)).fetchone()
        if r and cep and r[1] and cep != r[1]:
            log(c, "chain.epoch", peer, f"{chain}: {r[0]} -> neu ab {cseq}")       # Gegenstelle hat ein Backup zurueckgespielt
            r = None
        if r and cseq <= r[0]:
            return "stale"                   # aelter als schon Verarbeitetes (Reihenfolge je Kette)
        if r and cseq > r[0] + 1:
            log(c, "chain.gap", peer, f"{chain}: {r[0]} -> {cseq}", throttle=(peer["id"], "gap", chain))
        c.execute("INSERT INTO fed_chain_in (peer_id, chain, seq, ts, epoch) VALUES (?,?,?,?,?) ON CONFLICT (peer_id, chain) "
                  "DO UPDATE SET seq=EXCLUDED.seq, ts=EXCLUDED.ts, epoch=CASE WHEN EXCLUDED.epoch='' THEN fed_chain_in.epoch "
                  "ELSE EXCLUDED.epoch END", (peer["id"], chain, cseq, time.time(), cep))
    h = EV2_HANDLERS.get(ev.get("type"))
    if h is not None:
        return h(c, peer, ev)
    return _ev(c, peer, ev)


@router.post("/fed/v2/events")
async def fed_events2(request: Request):
    inp, err = await _verify(request)
    if err is not None:
        return err
    evs = (inp.data or {}).get("events") if isinstance(inp.data, dict) else None
    if not isinstance(evs, list) or len(evs) > EVENTS_MAX:
        return _signed(inp, 400, {"error": "bad events"})
    if not _rate.allow("ev:" + inp.peer["base_url"], EV_RATE):
        return _signed(inp, 429, {"error": "too many events"}, extra={"Retry-After": str(_rate.wait("ev:" + inp.peer["base_url"]))})

    def run():
        for fn in list(EV2_PREWORK):         # Netzwerk VOR der Transaktion (z. B. Schluessel bei verbundenen Dritt-Servern)
            try:
                fn(inp.peer, evs)
            except Exception as ex:  # noqa: BLE001 - Vorarbeit ist optional (sonst 'retry' im Zuhoerer)
                print(f"federation: prework {type(ex).__name__}: {ex}", flush=True)
        out = []
        with _db() as c, c:
            for ev in evs:
                out.append(_ev2(c, inp.peer, ev) if isinstance(ev, dict) else "ignored")
        return out
    return _signed(inp, 200, {"results": await run_in_threadpool(run)})


# =====================================================================================================
# Server-zu-Server: Inhalte im Namen einer Person (Eigentuemer-Seite)
# =====================================================================================================
EDIT_KEYS = ("title", "body", "format", "rev", "overwrite")
# 2.3 (Faehigkeit 'db'): zusaetzlich Werte von Datenbank-Zeilen, Schema, Symbol, Titelbild, Schlagworte, Breite
EDIT2_KEYS = EDIT_KEYS + ("props", "schema", "icon", "cover", "cover_pos", "tags", "wide", "font", "small")   # font/small: 2.5 (G9)
MERGE_KEYS = ("merge", "base", "bases")      # 2.5 (merge1): Zusammenfuehren beim Eigentuemer (pages._merge_req)


def edit_keys(peer, v2=True):
    """Felder, die eine Gegenstelle beim Speichern einer Seite hier mitschicken darf (mit merge1 auch merge/base/bases)."""
    return (EDIT2_KEYS if v2 else EDIT_KEYS) + (MERGE_KEYS if feature(peer, "merge1") else ())


def merge_fields(b):
    """merge/base/bases (Client bzw. Gegenstelle) gepruefte Form: merge nur 1, base nur "sent", bases hoechstens 500
    {Block-ID: rev}. Unbrauchbares faellt still weg (dann eben ohne Zusammenfuehren bzw. ohne feinere Basis)."""
    out = {}
    if b.get("merge") in (1, True):
        out["merge"] = 1
    if b.get("base") == "sent":
        out["base"] = "sent"
    if isinstance(b.get("bases"), dict):
        bs = {k: v for k, v in list(b["bases"].items())[:500]
              if isinstance(k, str) and BLOCK_ID_RE.fullmatch(k) and isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 2 ** 62}
        if bs:
            out["bases"] = bs
    return out


@router.get("/fed/v1/shared")
async def fed_shared(request: Request):
    inp, err = await _verify(request, need_actor=True)
    if err is not None:
        return err

    def run():
        items = _as(inp, sharing.shared_list)
        with _db() as c:
            return _ext_persons(c, items, peer=inp.peer)
    return _signed(inp, 200, await run_in_threadpool(run))


@router.get("/fed/v1/notes/{nid}")
async def fed_note_get(nid: int, request: Request):
    inp, err = await _verify(request, need_actor=True)
    if err is not None:
        return err
    return _relay(inp, await run_in_threadpool(_as, inp, pages.note_get, nid, fed_since(inp, request)))


def fed_since(inp, request):
    """2.5 (merge1): ?since=<rev> einer Gegenstelle mit merge1 -> nur geaenderte Bloecke (pages.since_delta)."""
    return pages.since_arg(request.query_params.get("since")) if feature(inp.peer, "merge1") else None


@router.patch("/fed/v1/notes/{nid}")
async def fed_note_patch(nid: int, request: Request):
    inp, err = await _verify(request, need_actor=True)
    if err is not None:
        return err
    b = inp.data if isinstance(inp.data, dict) else None
    if not b:
        return _signed(inp, 400, {"error": "bad request"})
    b = {k: v for k, v in b.items() if k in edit_keys(inp.peer, False)}      # ueber Instanzen: nur Titel und Inhalt
    b = dict({k: v for k, v in b.items() if k not in MERGE_KEYS}, **merge_fields(b))
    if not b:
        return _signed(inp, 400, {"error": t("nichts zu ändern")})
    return _relay(inp, await run_in_threadpool(_as, inp, pages._note_update, nid, b, None, peer_caps(inp.peer)))


def peer_caps(p):
    """Client-Faehigkeiten (pages.CLIENT_CAPS) einer Gegenstelle fuer das Speichern beim Eigentuemer."""
    return frozenset(caps_of(p) & pages.CLIENT_CAPS)


@router.get("/fed/v1/notes/{nid}/comments")
async def fed_comments(nid: int, request: Request, resolved: int = 0):
    inp, err = await _verify(request, need_actor=True)
    if err is not None:
        return err
    return _relay(inp, await run_in_threadpool(_as, inp, comments.comments_list, nid, 1 if resolved else 0))


@router.post("/fed/v1/notes/{nid}/comments")
async def fed_comment_add(nid: int, request: Request):
    inp, err = await _verify(request, need_actor=True)
    if err is not None:
        return err
    b = inp.data if isinstance(inp.data, dict) else None
    if b is None:
        return _signed(inp, 400, {"error": "bad request"})
    return _relay(inp, await run_in_threadpool(_as, inp, comments.comment_add, nid, b))


@router.patch("/fed/v1/comments/{cid}")
async def fed_comment_edit(cid: int, request: Request):
    inp, err = await _verify(request, need_actor=True)
    if err is not None:
        return err
    return _relay(inp, await run_in_threadpool(_as, inp, comments.comment_edit, cid, inp.data if isinstance(inp.data, dict) else {}))


@router.delete("/fed/v1/comments/{cid}")
async def fed_comment_delete(cid: int, request: Request, all: int = 0):
    inp, err = await _verify(request, need_actor=True)
    if err is not None:
        return err
    return _relay(inp, await run_in_threadpool(_as, inp, comments.comment_delete, cid, 1 if all else 0))


@router.post("/fed/v1/comments/{cid}/resolve")
async def fed_comment_resolve(cid: int, request: Request):
    inp, err = await _verify(request, need_actor=True)
    if err is not None:
        return err
    return _relay(inp, await run_in_threadpool(_as, inp, comments.comment_resolve, cid, inp.data if isinstance(inp.data, dict) else {}))


@router.post("/fed/v1/comments/{cid}/seen")
async def fed_comment_seen(cid: int, request: Request):
    inp, err = await _verify(request, need_actor=True)
    if err is not None:
        return err
    return _relay(inp, await run_in_threadpool(_as, inp, comments.comment_seen, cid))


def _file_for(principal, fid, nid, request):
    """Datei fid, wenn die fremde Person die Seite nid sehen darf und GENAU diese Seite die Datei benutzt
    (Inhalt, Symbol, Titelbild, 2.5: Datei-Eigenschaft in props) und sie dem Eigentuemer der Seite gehoert. -> (bytes, mime, name, is_image) oder None."""
    if not _FID.fullmatch(fid or "") or not (0 < nid < 2 ** 63):
        return None
    with _db() as c:
        acc = sharing.Access(c, principal)
        if not acc.role(nid):
            return None
        n = c.execute("SELECT uid, body, icon, cover, props FROM note WHERE id=? AND deleted_at IS NULL", (nid,)).fetchone()
        f = c.execute("SELECT name, mime, is_image, uid, fed_by, fed_note FROM file WHERE id=?", (fid,)).fetchone()
    ref_ = f"/files/{fid}"
    # 2.3: gerade von dieser Person fuer diese Seite hochgeladen (Vorschau im Editor, noch nicht gespeichert)
    fresh = bool(f) and f[4] == principal and f[5] == nid
    if not n or not f or f[3] != n[0] or not (fresh or ref_ in (n[1] or "") or n[2] == ref_ or n[3] == ref_ or ref_ in (n[4] or "")):
        return None
    path = os.path.join(FILES_DIR, fid)
    if not os.path.isfile(path) or os.path.getsize(path) > FILE_MAX + 1024:
        return None
    mime = f[1]
    if f[2]:
        w = _thumb_w(request)
        tp = _thumb(fid, path, mime, w) if w else None
        if tp:
            path, mime = tp, _thumb_mime(tp)
    with open(path, "rb") as fh:
        return fh.read(), mime, f[0], bool(f[2])


@router.get("/fed/v1/files/{fid}")
async def fed_file(fid: str, request: Request, note: int = 0):
    inp, err = await _verify(request, need_actor=True)
    if err is not None:
        return err
    r = await run_in_threadpool(_as, inp, _file_for, fid, note, request)
    if not r:
        return _signed(inp, 404, b"not found", "text/plain")
    data, mime, name, img = r
    return _signed(inp, 200, data, mime if img else "application/octet-stream",
                   {"Content-Disposition": "inline" if img else _disposition(name)})


# Profilbilder (2.1): eine verbundene Instanz holt das Bild einer Person (oeffentlicher Name) - signiert, neu kodiert (WebP,
# 256 px). Die andere Instanz kodiert es noch einmal neu und speichert es einen Tag zwischen (saga.avatars).
AVATAR_MAX = 512 * 1024


DIR_VISIBLE = None        # von saga.fed_dir: dir_visible(c, uid, peer) - steht das Konto im Adressbuch fuer peer?


def _avatar_for(name, peer=None):
    if not is_name(name):
        return None
    with _db() as c:
        uid, _ = _local_user_by_name(c, name)
        # pseudonymer Handle: Bild nur, wenn das Konto im Adressbuch dieser Instanz steht (Schalter je Konto/Instanz)
        if uid and PSEUDO_RE.fullmatch(name) and not (DIR_VISIBLE is not None and peer is not None and DIR_VISIBLE(c, uid, peer)):
            uid = None
    return avatars.local_image(uid, 256, "webp") if uid else None


@router.get("/fed/v1/avatar/{name}")
async def fed_avatar(name: str, request: Request):
    inp, err = await _verify(request)
    if err is not None:
        return err
    got = await run_in_threadpool(_avatar_for, name, inp.peer)
    if not got:
        return _signed(inp, 404, b"not found", "text/plain")
    return _signed(inp, 200, got[1], got[0])


def fetch_avatar(principal):
    """Fuer saga.avatars: Bild einer fremden Person bei ihrer Instanz holen -> bytes | None (kein Bild). FedError = spaeter."""
    n, host = _handle(principal)
    if not n:
        return None
    with _db() as c:
        p = peer_by(c, "host", host)
    if not enabled() or not p or p["status"] != "active":
        return None
    try:
        status, rh, data = call(p, "GET", "/fed/v1/avatar/" + quote(n, safe=""), limit=AVATAR_MAX, timeout=6)
    except FedError as ex:
        if ex.code == 404:         # aeltere Gegenstelle ohne Profilbilder (unsignierte 404): kein Bild
            return None
        raise
    if status == 404:
        return None
    if status != 200:
        raise FedError(f"HTTP {status}", status)
    return data


avatars.REMOTE_FETCH = fetch_avatar


# =====================================================================================================
# Ausgehende Ereignisse: Ausgangskorb + Arbeiter
# =====================================================================================================
_wake = threading.Event()
_worker = {"thread": None, "stop": False, "house": 0.0, "hello": 0.0}
# Express-Zustellung (Protokoll 2): eigener, begrenzter Thread-Pool - eine langsame Gegenstelle blockiert weder Anfragen
# noch den Ausgangskorb-Arbeiter. Faellt die Zustellung aus (Frist, Fehler), bleibt das Ereignis im Ausgangskorb.
_express = ThreadPoolExecutor(max_workers=max(2, int(E.get("FED_EXPRESS_WORKERS", "8"))), thread_name_prefix="saga-fed-express")
EV2_HANDLERS = {}            # Ereignis-Art -> fn(c, peer, ev) -> 'ok' | 'ignored' (saga.fed_dir, saga.fed_keys)
EV2_HANDLERS["person.renamed"] = _ev_renamed      # 2.5 (rename), oben
EV2_PREWORK = []             # fn(peer, events) - vor der Transaktion von POST /fed/v2/events (saga.fed_chat)
PERIODIC = []                # fn() - im Arbeiter-Thread nach jedem Durchlauf (eigene Taktung, Fehler werden protokolliert)
HELLO_HOOKS = []             # fn(peer_id) - Faehigkeiten oder Admin-Schalter einer Instanz haben sich geaendert (im Thread)
PEER_GONE_HOOKS = []         # fn(c, peer) - Verbindung getrennt (in der Transaktion von cleanup_peer)
PERSON_GONE_HOOKS = []       # fn(c, peer, name, deleted) - Person einer anderen Instanz weg (Ereignis person.gone)


def _enqueue(c, peer_id, ev, chain=None, express=True):
    """Ereignis fuer eine Instanz vormerken (in der Transaktion). Jedes Ereignis bekommt eine Kennung (id, Empfaenger
    verarbeitet es hoechstens einmal); chain: Ereignisse derselben Kette gehen streng der Reihe nach (cseq). Nach dem
    Commit: Express-Zustellung, wenn die Gegenstelle sie kann - sonst (und bei Fehlern) der Arbeiter mit Wiederholung.
    -> Zeilen-ID im Ausgangskorb."""
    now = time.time()
    ev = dict(ev, id=secrets.token_hex(16))
    if chain:
        # Epoche je Kette (zufaellig): nach dem Zurueckspielen eines Backups neu (check_epoch) - die Gegenstelle beginnt
        # die Kette dann neu, statt die wieder kleinen Nummern still als "schon gesehen" zu verwerfen
        seq, ep = c.execute("INSERT INTO fed_chain_out (peer_id, chain, seq, epoch) VALUES (?,?,1,?) ON CONFLICT (peer_id, chain) "
                            "DO UPDATE SET seq=fed_chain_out.seq+1, epoch=CASE WHEN fed_chain_out.epoch='' THEN EXCLUDED.epoch "
                            "ELSE fed_chain_out.epoch END RETURNING seq, epoch", (peer_id, chain, secrets.token_hex(8))).fetchone()
        ev.update(chain=chain, cseq=int(seq), cep=ep)
    oid = c.execute("INSERT INTO fed_outbox (peer_id, payload, created, next_try, chain, ev_id) VALUES (?,?,?,?,?,?)",
                    (peer_id, json.dumps(ev, ensure_ascii=False), now, now, chain, ev["id"])).lastrowid
    after = getattr(c, "after_commit", None)
    if after:
        after((lambda: _express_submit(peer_id, oid)) if express else _wake.set)
    return oid


# --- Je Instanz begrenzt: eine langsame oder boesartige Gegenstelle belegt hoechstens PEER_SLOTS Threads des Express-Pools ---
_slots_lock = threading.Lock()
_slots = {}                  # peer_id -> laufende Auftraege
_backlog = {}                # peer_id -> wartende Auftraege [(fn, args)] (begrenzt)
BACKLOG_MAX = 200


def peer_submit(peer_id, fn, *args, drop=False):
    """Hintergrund-Auftrag fuer eine Instanz im Express-Pool. Laufen fuer sie schon PEER_SLOTS Auftraege, wartet er (drop:
    wird verworfen - Tippen, Live-Hinweise). -> Future, None (wartet/verworfen) oder False (Pool beendet)."""
    with _slots_lock:
        if _slots.get(peer_id, 0) >= PEER_SLOTS:
            if drop:
                return None
            q = _backlog.setdefault(peer_id, [])
            if len(q) >= BACKLOG_MAX:
                q.pop(0)                     # aeltester Auftrag weg (alle sind wiederholbar: Luecke, Vorabruf, Abgleich)
            q.append((fn, args))
            return None
        _slots[peer_id] = _slots.get(peer_id, 0) + 1
    try:
        return _express.submit(_peer_run, peer_id, fn, args)
    except RuntimeError:                     # Pool beendet (Herunterfahren)
        with _slots_lock:
            _slots[peer_id] -= 1
        return False


def _peer_run(peer_id, fn, args):
    try:
        return fn(*args)
    finally:                                 # Platz an den naechsten wartenden Auftrag dieser Instanz weitergeben
        with _slots_lock:
            q = _backlog.get(peer_id)
            nxt = q.pop(0) if q else None
            if not q:
                _backlog.pop(peer_id, None)
            if nxt is None:
                _slots[peer_id] -= 1
                if _slots[peer_id] <= 0:
                    del _slots[peer_id]
        if nxt is not None:
            try:
                _express.submit(_peer_run, peer_id, nxt[0], nxt[1])
            except RuntimeError:
                with _slots_lock:
                    _slots[peer_id] = max(0, _slots.get(peer_id, 1) - 1)


# --- Backoff je Instanz (HTTP 429 der Gegenseite, Retry-After) ---------------------------------------------------------------
_backoff = {}                # peer_id -> (bis, stufe)


def backoff_until(peer_id):
    b = _backoff.get(peer_id)
    return b[0] if b and b[0] > time.time() else 0


def _note_busy(peer_id, after=None):
    """Gegenstelle bremst (429): warten - Retry-After, sonst 15 s verdoppelt bis 10 min. -> Zeitpunkt."""
    _, n = _backoff.get(peer_id, (0, 0))
    wait = after if after else min(600, 15 * 2 ** min(n, 6))
    until = time.time() + wait
    _backoff[peer_id] = (until, n + 1)
    return until


def _express_submit(peer_id, oid):
    if backoff_until(peer_id):
        _wake.set()
        return None
    f = peer_submit(peer_id, _express_one, peer_id, oid, drop=True)
    if not f:                                # Instanz ausgelastet (oder Pool beendet): der Arbeiter stellt zu
        _wake.set()
        return None
    return f


def _events_call(p, events, timeout=TIMEOUT):
    """Ereignisse senden -> (status, antwort, retry_after). FedError bei Netz-/Signaturfehlern."""
    path = "/fed/v2/events" if "v2" in caps_of(p) else "/fed/v1/events"
    status, rh, data = call(p, "POST", path, body=json.dumps({"events": events}, ensure_ascii=False).encode(), timeout=timeout)
    try:
        d = parse_json(data) if data else {}
    except (ValueError, RecursionError):
        raise FedError("ungueltige Antwort", status)
    if not pages._json_depth_ok(d, 32):
        raise FedError("ungueltige Antwort", status)
    return status, d, retry_after(rh.get("retry-after")) if status == 429 else None


def _post_events(p, events, timeout=TIMEOUT):
    """Ereignisse an eine Instanz senden -> None (zugestellt) oder Fehlertext ('busy:<s>' = Gegenseite bremst).
    Protokoll 2: /fed/v2/events, sonst v1."""
    try:
        status, d, ra = _events_call(p, events, timeout)
    except FedError as ex:
        if ex.code == 429:
            return f"busy:{ex.retry_after or 0}"
        return str(ex)
    if status == 429:
        return f"busy:{ra or 0}"
    if status != 200:
        return f"HTTP {status}"
    res = d.get("results") if isinstance(d, dict) else None
    if isinstance(res, list) and "retry" in res:
        return "retry"
    return None


def _express_one(peer_id, oid):
    """Eine Zeile des Ausgangskorbs sofort zustellen (harte Frist EXPRESS_TIMEOUT). -> True = zugestellt."""
    now = time.time()
    with _db() as c:
        p = peer_by(c, "id", peer_id)
    if not feature(p, "express") or backoff_until(peer_id):
        _wake.set()
        return False
    with _db() as c, c:
        r = c.execute("UPDATE fed_outbox o SET locked_until=? WHERE id=? AND locked_until<? AND (chain IS NULL OR NOT EXISTS "
                      "(SELECT 1 FROM fed_outbox o2 WHERE o2.peer_id=o.peer_id AND o2.chain=o.chain AND o2.id<o.id)) RETURNING payload",
                      (now + 3 * EXPRESS_TIMEOUT + 30, oid, now)).fetchone()
    if not r:                                # schon zugestellt, gesperrt oder eine fruehere Zeile der Kette wartet
        _wake.set()
        return False
    err = _post_events(p, [json.loads(r[0])], timeout=max(1.0, EXPRESS_TIMEOUT / 3))
    with _db() as c, c:
        if err is None:
            _backoff.pop(peer_id, None)
            c.execute("DELETE FROM fed_outbox WHERE id=?", (oid,))
            _touch(c, p)
            return True
        if err.startswith("busy:"):          # Gegenseite bremst: kein Fehlversuch, ganze Instanz wartet
            until = _note_busy(peer_id, int(err[5:]) or None)
            c.execute("UPDATE fed_outbox SET next_try=?, locked_until=0, last_error=? WHERE id=?", (until, "busy", oid))
            return False
        _touch(c, p, err)
        c.execute("UPDATE fed_outbox SET attempts=attempts+1, next_try=?, locked_until=0, last_error=? WHERE id=?", (now + 15, err[:300], oid))
    return False


def send_now(peer_id, ev, chain=None, timeout=None):
    """Synchron zustellen (Express, harte Frist): -> True, wenn die Gegenstelle das Ereignis bestaetigt hat. Sonst liegt
    es im Ausgangskorb und wird spaeter (in Reihenfolge der Kette) zugestellt."""
    with _db() as c, c:
        oid = _enqueue(c, peer_id, ev, chain, express=False)
    fut = _express_submit(peer_id, oid)
    if fut is None:
        return False
    try:
        return bool(fut.result(timeout=timeout or EXPRESS_TIMEOUT))
    except Exception:  # noqa: BLE001 - Frist abgelaufen: bleibt im Ausgangskorb
        return False


def _target_peer(c, target):
    """'@name@host' bzw. 'gruppe@host' -> (aktiver peer, name) oder (None, None)."""
    name, _, host = target.lstrip("@").rpartition("@")
    p = peer_by(c, "host", host) if name and host else None
    return (p, name) if p and p["status"] == "active" else (None, None)


def _owner_info(c, uid, p=None):
    pub = fed_name(c, uid, p)
    return {"name": pub, "display": display_of(c, uid) or pub}


def _note_title(c, nid):
    r = c.execute("SELECT title FROM note WHERE id=?", (nid,)).fetchone()
    return (r[0] or "") if r else ""


def _on_share_set(c, owner, note_id, target_type, target, role, old_role=None, **_):
    if target_type not in sharing.REMOTE_TYPES or old_role == role or not enabled():
        return
    p, name = _target_peer(c, target)
    if not p:
        return
    o = _owner_info(c, owner, p)
    if not o["name"]:
        return
    _enqueue(c, p["id"], {"type": "share.role" if old_role else "share.added", "to": {"type": "user" if target_type == "remote" else "group", "name": name},
                          "owner": o, "note": {"id": note_id, "title": _note_title(c, note_id)}, "role": role})
    log(c, "share.set", p, f"note={note_id} {target_type} role={role}")


def _on_share_removed(c, owner, note_id, target_type, target, **_):
    if target_type not in sharing.REMOTE_TYPES:
        return
    p, name = _target_peer(c, target)
    if not p:
        return
    _enqueue(c, p["id"], {"type": "share.removed", "to": {"type": "user" if target_type == "remote" else "group", "name": name},
                          "note": {"id": note_id}})
    log(c, "share.removed", p, f"note={note_id} {target_type}")


def _handle_for(c, uid, p=None):
    """Ausloeser fuer andere Instanzen: {handle: 'name@host', display} - lokale Konten nur mit oeffentlichem Namen
    (Protokoll 2 mit 'handles': sonst mit pseudonymem Handle)."""
    if not uid:
        return None
    if is_remote(uid):
        r = c.execute("SELECT display FROM fed_person WHERE principal=?", (uid,)).fetchone()
        return {"handle": uid[1:], "display": (r[0] if r else "") or uid[1:]}
    pub = fed_name(c, uid, p)
    if not pub:
        return None
    return {"handle": f"{pub}@{SELF_HOST}", "display": display_of(c, uid) or pub}


def _on_notify(c, uid, kind, actor=None, note_id=None, comment_id=None, data=None, **_):
    """Benachrichtigung fuer eine fremde Person (saga.notify) -> signiertes Ereignis an ihre Instanz."""
    if kind not in ("comment.new", "comment.reply", "db.assigned", "page.mention", "comment.mention") or not note_id or not enabled():
        return
    p, name = _target_peer(c, uid)
    if not p or not sharing.Access(c, uid).role(note_id):
        return
    if kind in ("page.mention", "comment.mention") and not feature(p, "ment1"):
        return                               # 2.5 (P6-5): aeltere Instanzen kennen keine Erwaehnungen
    if kind == "page.mention":
        _enqueue(c, p["id"], {"type": kind, "to": {"type": "user", "name": name}, "actor": _handle_for(c, actor, p),
                              "note": {"id": note_id, "title": _note_title(c, note_id)},
                              "block": str((data or {}).get("block") or "")[:40]})
        return
    if kind == "db.assigned":                # 2.5 (dbx1): in einer Datenbank-Zeile eingetragen - nur an Instanzen mit dbx1
        if feature(p, "dbx1"):
            par = c.execute("SELECT parent_id FROM note WHERE id=?", (note_id,)).fetchone()
            _enqueue(c, p["id"], {"type": kind, "to": {"type": "user", "name": name}, "actor": _handle_for(c, actor, p),
                                  "note": {"id": note_id, "title": _note_title(c, note_id), "db": par[0] if par else None}})
        return
    cm = c.execute("SELECT thread_id, block_id FROM note_comment WHERE id=?", (comment_id,)).fetchone() if comment_id else None
    _enqueue(c, p["id"], {"type": kind, "to": {"type": "user", "name": name}, "actor": _handle_for(c, actor, p),
                          "note": {"id": note_id, "title": _note_title(c, note_id)},
                          "comment": {"id": comment_id, "thread": (cm[0] or comment_id) if cm else comment_id, "block": (cm[1] if cm else "") or ""}})


def _on_name_released(c, uid, pub=None, deleted=False, **_):
    """Konto geloescht (bzw. Name aufgegeben): Eingangs-Verweise weg; war die Foederation je aktiv, Name sperren (nie an
    ein anderes Konto) und alle verbundenen Instanzen informieren (Freigaben an den alten Namen enden dort).
    Umbenennen laeuft ueber public_name.renamed (_on_name_renamed)."""
    c.execute("DELETE FROM fed_inbound WHERE target_type='user' AND target=?", (uid,))
    if deleted and has_key(c):           # Protokoll 2: auch der pseudonyme Handle verschwindet (Freigaben/Adressbuch dort)
        h = c.execute("SELECT handle FROM fed_handle WHERE uid=?", (uid,)).fetchone()
        if h:
            for p in peers(c):
                if p["status"] == "active" and "handles" in caps_of(p):
                    _enqueue(c, p["id"], {"type": "person.gone", "name": h[0], "deleted": True})
    if deleted:                          # fruehere Namen (Umbenennen) gehoeren niemandem mehr - auch nicht einem neuen Konto mit gleichem Anmeldenamen
        c.execute("UPDATE fed_name_retired SET uid=NULL WHERE uid=?", (uid,))
    if not pub or not has_key(c):
        return
    c.execute("INSERT OR REPLACE INTO fed_name_retired (name, uid, retired) VALUES (?,?,?)", (pub, None if deleted else uid, time.time()))
    for p in peers(c):
        if p["status"] == "active":
            _enqueue(c, p["id"], {"type": "person.gone", "name": pub, "deleted": bool(deleted)})


def _on_name_renamed(c, uid, old=None, new=None, **_):
    """Oeffentlichen Namen geaendert (old -> new) bzw. erstmals gesetzt (old None; bisher kannten Instanzen mit 'handles'
    die Person unter ihrem Handle). Instanzen mit 'rename' bekommen person.renamed (Freigaben bleiben, beide Richtungen),
    aeltere wie bisher person.gone - ihre Freigaben an den alten Namen enden (Eingangs-Verweise von dort weg)."""
    if not new or old == new:
        return
    if old and has_key(c):               # alter Name nie an ein anderes Konto (Ereignisse unterwegs erreichen weiter diese Person)
        c.execute("INSERT OR REPLACE INTO fed_name_retired (name, uid, retired) VALUES (?,?,?)", (old, uid, time.time()))
    if not has_key(c):
        return
    h = None
    for p in peers(c):
        caps = caps_of(p)
        if p["status"] == "active" and "rename" in caps:
            prev = old or (c.execute("SELECT handle FROM fed_handle WHERE uid=?", (uid,)).fetchone() or [None])[0]
            if prev and "handles" not in caps and not old:
                prev = None
            if prev and not _knows_person(c, uid, p):
                prev = None                  # kennt die Person nicht: Handle und Namen nicht verraten (wie op_resolve, Adressbuch)
            if prev:
                h = h or own_handle(c, uid)
                _enqueue(c, p["id"], {"type": "person.renamed", "old": prev, "new": new, "handle": h}, chain="person:" + h[1:])
            continue
        if old:
            c.execute("DELETE FROM fed_inbound WHERE peer_id=? AND target_type='user' AND target=?", (p["id"], uid))
            if p["status"] == "active":
                _enqueue(c, p["id"], {"type": "person.gone", "name": old, "deleted": False})


def _knows_person(c, uid, p):
    """Kennt Instanz p das Konto uid schon - steht es in ihrem Adressbuch, hat p mit ihm (bzw. einer seiner Gruppen)
    geteilt oder es mit Personen/Gruppen von p? Nur dann bekommt p beim Umbenennen person.renamed (mit Handle)."""
    if DIR_VISIBLE is not None and DIR_VISIBLE(c, uid, p):
        return True
    gs = sorted(sharing.groups_of(c, uid))
    q = "SELECT 1 FROM fed_inbound WHERE peer_id=? AND ((target_type='user' AND target=?)" + \
        (f" OR (target_type='group' AND target IN ({','.join('?' * len(gs))}))" if gs else "") + ") LIMIT 1"
    if c.execute(q, (p["id"], uid, *gs)).fetchone():
        return True
    return bool(c.execute("SELECT 1 FROM note_share WHERE owner_uid=? AND target_type IN ('remote','remote_group') AND target LIKE ? LIMIT 1",
                          (uid, "%" + _host_tail(p["host"]))).fetchone())


def rename_loss(c, uid):
    """Was beim Umbenennen verloren ginge (Instanzen ohne 'rename'): {n: Seiten, hosts: [...]} - fuer den Hinweis im
    Konto. Ohne oeffentlichen Namen kennen solche Instanzen die Person hoechstens unter ihrem Handle (dann ebenso)."""
    if not enabled():
        return {"n": 0, "hosts": []}
    n, hosts = 0, []
    for p in peers(c):
        if p["status"] != "active" or "rename" in caps_of(p):
            continue
        k = c.execute("SELECT COUNT(*) FROM fed_inbound WHERE peer_id=? AND target_type='user' AND target=?", (p["id"], uid)).fetchone()[0]
        if k:
            n += k
            hosts.append(p["host"])
    return {"n": n, "hosts": sorted(hosts)}


bus.on("share.set", _on_share_set)
bus.on("share.removed", _on_share_removed)
bus.on("fed.notify", _on_notify)
bus.on("public_name.released", _on_name_released)
bus.on("public_name.renamed", _on_name_renamed)


def _pick_due(c, now, limit=60):
    """Faellige Zeilen waehlen und sperren. Ketten: nur ein lueckenloser Anfang (die aelteste offene Zeile der Kette und
    die direkt folgenden) - eine wartende/gesperrte fruehere Zeile haelt den Rest der Kette zurueck."""
    rows = c.execute("SELECT id, peer_id, payload, attempts, created, chain FROM fed_outbox WHERE next_try<=? AND locked_until<? "
                     "ORDER BY id LIMIT 300 FOR UPDATE SKIP LOCKED", (now, now)).fetchall()
    due = {r[0] for r in rows}
    ok_ids = set()
    for pid, ch in {(r[1], r[5]) for r in rows if r[5]}:
        for (i,) in c.execute("SELECT id FROM fed_outbox WHERE peer_id=? AND chain=? ORDER BY id LIMIT 300", (pid, ch)).fetchall():
            if i not in due:
                break
            ok_ids.add(i)
    pick = [r for r in rows if not r[5] or r[0] in ok_ids][:limit]
    if pick:
        c.execute(f"UPDATE fed_outbox SET locked_until=? WHERE id IN ({','.join('?' * len(pick))})", (now + 120, *[r[0] for r in pick]))
    return pick


def _post_events_r(p, events, timeout=TIMEOUT):
    """-> (fehler, ergebnisse): fehler None (zugestellt; ergebnisse je Ereignis), 'net:...' (Instanz nicht erreichbar),
    'busy:<s>' (Instanz bremst, HTTP 429 - Retry-After) oder 'HTTP n' / 'bad response' (die Instanz hat die Sendung abgewiesen)."""
    try:
        status, d, ra = _events_call(p, events, timeout)
    except FedError as ex:
        if ex.code == 429:
            return f"busy:{ex.retry_after or 0}", None
        return ("net:" if ex.code is None else f"HTTP {ex.code}: ") + str(ex), None
    if status == 429:
        return f"busy:{ra or 0}", None
    if status != 200:
        return f"HTTP {status}", None
    res = d.get("results") if isinstance(d, dict) else None
    if not isinstance(res, list) or len(res) != len(events):
        return "bad response", None
    return None, res


STUCK_AFTER = 6              # so viele Fehlversuche einer Zeile -> Pruefprotokoll "event.stuck" (Kette steht, Rest laeuft)
V1_TYPES = frozenset(("share.added", "share.role", "share.removed", "comment.new", "comment.reply", "person.gone"))


def _needs_v2(r):
    try:
        return json.loads(r[2]).get("type") not in V1_TYPES
    except (ValueError, AttributeError):
        return False


def _row_failed(c, p, r, err, now):
    """Eine Zeile des Ausgangskorbs ist (noch) nicht zugestellt: spaeter erneut (Backoff) bzw. nach OUTBOX_KEEP verworfen."""
    if r[4] < now - OUTBOX_KEEP:
        c.execute("DELETE FROM fed_outbox WHERE id=?", (r[0],))
        log(c, "event.dropped", p, (f"chain={r[5]} " if r[5] else "") + err)
        return
    c.execute("UPDATE fed_outbox SET attempts=attempts+1, next_try=?, locked_until=0, last_error=? WHERE id=?",
              (now + min(3600, 15 * 2 ** min(r[3], 12)), err[:300], r[0]))
    if r[3] + 1 == STUCK_AFTER:
        log(c, "event.stuck", p, (f"chain={r[5]} " if r[5] else "") + err)


_out_pool = ThreadPoolExecutor(max_workers=max(2, int(E.get("FED_OUT_WORKERS", "4"))), thread_name_prefix="saga-fed-out")
_out_busy, _out_lock = set(), threading.Lock()


def deliver_due(wait=True):
    """Faellige Ereignisse senden (mehrere Prozesse: Zeilen werden reserviert). -> Anzahl zugestellter (wait=False: 0).
    Ist die Instanz nicht erreichbar, wartet alles fuer sie. Weist sie dagegen einzelne Ereignisse ab (Fehler, 'retry'),
    trifft das nur diese Zeilen und den Rest ihrer Kette ("Totmann" je Zeile) - andere Ereignisse und Ketten laufen weiter.
    wait=False (Arbeiter): je Instanz ein eigener Auftrag - eine langsame Gegenstelle haelt die anderen nicht auf."""
    now = time.time()
    with _db() as c, c:
        rows = _pick_due(c, now)
    if not rows:
        return 0
    by = {}
    for r in rows:
        by.setdefault(r[1], []).append(r)
    if wait:
        return sum(_deliver_peer(pid, lst, now) for pid, lst in by.items())
    for pid, lst in by.items():
        with _out_lock:
            busy = pid in _out_busy
            if not busy:
                _out_busy.add(pid)
        if busy:                                         # laeuft schon (langsame Instanz): Zeilen freigeben, spaeter
            with _db() as c, c:
                c.execute(f"UPDATE fed_outbox SET locked_until=0 WHERE id IN ({','.join('?' * len(lst))})", tuple(r[0] for r in lst))
            continue
        try:
            _out_pool.submit(_deliver_bg, pid, lst, now)
        except RuntimeError:                             # Pool beendet (Herunterfahren)
            with _out_lock:
                _out_busy.discard(pid)
    return 0


def _deliver_bg(pid, lst, now):
    try:
        if _deliver_peer(pid, lst, now) == len(lst) and len(lst) >= EVENTS_MAX:
            _wake.set()                                  # (viel zu tun: gleich weiter)
    except Exception as ex:  # noqa: BLE001
        print(f"federation: Zustellung: {type(ex).__name__}: {ex}", flush=True)
    finally:
        with _out_lock:
            _out_busy.discard(pid)


def _deliver_peer(pid, lst, now):
    """Zeilen des Ausgangskorbs fuer eine Instanz zustellen -> Anzahl zugestellter."""
    sent = 0
    with _db() as c:
        p = peer_by(c, "id", pid)
    if not p or p["status"] != "active":
        with _db() as c, c:
            c.execute(f"DELETE FROM fed_outbox WHERE id IN ({','.join('?' * len(lst))})", tuple(r[0] for r in lst))
        return 0
    down, held = None, set()
    queue = [lst[k:k + EVENTS_MAX] for k in range(0, len(lst), EVENTS_MAX)]
    while queue:
        part = queue.pop(0)
        skip = [r for r in part if r[5] and r[5] in held]
        part = [r for r in part if not (r[5] and r[5] in held)]
        if skip:                                     # fruehere Zeile der Kette steckt: diese spaeter (Reihenfolge)
            with _db() as c, c:
                c.execute(f"UPDATE fed_outbox SET locked_until=0 WHERE id IN ({','.join('?' * len(skip))})", tuple(r[0] for r in skip))
        if not part:
            continue
        if "v2" not in caps_of(p):              # Protokoll 1 (Rueckfall): Ereignisse von Protokoll 2 warten (nicht still verloren)
            hold = [r for r in part if _needs_v2(r)]
            if hold:
                part = [r for r in part if not _needs_v2(r)]
                with _db() as c, c:
                    for r in hold:
                        _row_failed(c, p, r, "protocol 2 required", now)
                        if r[5]:
                            held.add(r[5])
                if not part:
                    continue
        bu = backoff_until(pid)
        if bu:                                       # Instanz bremst (429): alles fuer sie wartet bis dahin - kein Fehlversuch
            with _db() as c, c:
                c.execute(f"UPDATE fed_outbox SET next_try=GREATEST(next_try, ?), locked_until=0 WHERE id IN ({','.join('?' * len(part))})",
                          (bu, *[r[0] for r in part]))
            continue
        err, res = (down, None) if down else _post_events_r(p, [json.loads(r[2]) for r in part])
        if err and err.startswith("busy:"):
            _note_busy(pid, int(err[5:]) or None)
            with _db() as c, c:
                log(c, "peer.busy", p, f"retry_after={err[5:]}", throttle=(pid, "busy"))
            queue.insert(0, part)                    # (oben: wartet mit allen weiteren Zeilen der Instanz)
            continue
        if err and not err.startswith("net:") and len(part) > 1:
            queue = [[r] for r in part] + queue          # abgewiesen: einzeln versuchen, um die eine Zeile zu finden
            continue
        with _db() as c, c:
            if err is None:
                _backoff.pop(pid, None)
                done = [r[0] for r, x in zip(part, res) if x != "retry"]
                if done:
                    c.execute(f"DELETE FROM fed_outbox WHERE id IN ({','.join('?' * len(done))})", tuple(done))
                    sent += len(done)
                _touch(c, p)
                for r, x in zip(part, res):
                    if x == "retry":
                        _row_failed(c, p, r, "retry", now)
                        if r[5]:
                            held.add(r[5])
                continue
            if err.startswith("net:"):
                if down is None:
                    _touch(c, p, err[4:])
                down = err
            for r in part:
                _row_failed(c, p, r, err[4:] if err.startswith("net:") else err, now)
                if r[5]:
                    held.add(r[5])
    return sent


def housekeeping():
    now = time.time()
    with _db() as c, c:
        c.execute("DELETE FROM fed_nonce WHERE created < ?", (now - 2 * fedsig.SKEW - 60,))
        c.execute("DELETE FROM fed_invite WHERE expires < ? AND used_at IS NULL", (now - 7 * 86400,))
        c.execute("DELETE FROM fed_log WHERE ts < ?", (now - 180 * 86400,))
        c.execute("DELETE FROM fed_event_seen WHERE ts < ?", (now - SEEN_KEEP,))


def _hello_due():
    """Faehigkeiten verbundener Instanzen nachfragen: noch nie oder seit HELLO_EVERY nicht mehr (Protokoll 2)."""
    now = time.time()
    with _db() as c:
        due = [p["id"] for p in peers(c) if p["status"] == "active" and (not p["caps_at"] or now - p["caps_at"] > HELLO_EVERY)]
    for pid in due[:20]:
        peer_submit(pid, hello, pid)         # je Instanz im Express-Pool (eine langsame haelt den Arbeiter nicht auf)


def _loop():
    while not _worker["stop"]:
        _wake.wait(5)
        _wake.clear()
        if _worker["stop"]:
            break
        try:
            if not enabled():
                continue
            deliver_due(wait=False)
            if time.time() - _worker["house"] > 600:
                _worker["house"] = time.time()
                housekeeping()
            if time.time() - _worker["hello"] > 60:
                _worker["hello"] = time.time()
                _hello_due()
            for fn in list(PERIODIC):
                try:
                    fn()
                except Exception as ex:  # noqa: BLE001 - ein Zusatz darf den Ausgangskorb nicht anhalten
                    print(f"federation: {getattr(fn, '__module__', '?')}.{getattr(fn, '__name__', '?')}: {type(ex).__name__}: {ex}", flush=True)
        except Exception as ex:  # noqa: BLE001 - Datenbank kurz weg o. ae.: naechster Durchlauf
            print(f"federation: {type(ex).__name__}: {ex}", flush=True)


def check_epoch():
    """Wurde ein Backup zurueckgespielt (oder die Datenbank umgezogen)? Dann stimmen die Ketten-Nummern nicht mehr mit dem
    ueberein, was andere Instanzen schon gesehen haben: neue Epoche je Kette. Erkannt ueber eine Marke, die bei jedem Start
    in der Datenbank UND im Daten-Verzeichnis neu gesetzt wird - nach dem Zurueckspielen passen beide nicht mehr zusammen."""
    path = os.path.join(STATE_DIR, "fed-epoch")
    try:
        with open(path) as f:
            mark = f.read().strip()
    except OSError:
        mark = ""
    new = secrets.token_hex(12)
    with _db() as c, c:
        r = c.execute("SELECT v FROM setting WHERE k='fed_epoch_mark'").fetchone()
        db_mark = json.loads(r[0]) if r and r[0] else ""
        if db_mark != mark and c.execute("SELECT 1 FROM fed_chain_out LIMIT 1").fetchone():
            n = c.execute("UPDATE fed_chain_out SET epoch=md5(random()::text || chain || peer_id::text)").rowcount
            log(c, "chain.epoch.new", None, f"chains={n}", host=SELF_HOST)
            print(f"federation: Ketten mit neuer Epoche ({n}) - Datenbank stammt aus einem Backup oder ist umgezogen", flush=True)
        c.execute("INSERT INTO setting (k, v) VALUES ('fed_epoch_mark', ?) ON CONFLICT (k) DO UPDATE SET v=EXCLUDED.v", (json.dumps(new),))
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(path + ".tmp", "w") as f:
            f.write(new)
        os.replace(path + ".tmp", path)
    except OSError as ex:
        print(f"federation: Epochen-Marke nicht gespeichert ({type(ex).__name__})", flush=True)


def start():
    if RESTORE_TEST:
        # Restore-Test: keine Foederation, auch keine neue Epoche (die Marke im Daten-Verzeichnis bleibt unberuehrt;
        # wird diese Datenbank spaeter produktiv, erkennt der naechste normale Start den Umzug)
        return
    try:
        check_epoch()
    except Exception as ex:  # noqa: BLE001 - Datenbank beim Start kurz weg: dann beim naechsten Start
        print(f"federation: Epochen-Pruefung: {type(ex).__name__}: {ex}", flush=True)
    if _worker["thread"] is None:
        _worker["stop"] = False
        th = threading.Thread(target=_loop, name="saga-federation", daemon=True)
        _worker["thread"] = th
        th.start()


def stop():
    _worker["stop"] = True
    _wake.set()
    _worker["thread"] = None


# =====================================================================================================
# Heimatserver: Proxy fuer die eigenen Konten (Weboberflaeche, Apps)
# =====================================================================================================
class FedId(Convertor):
    """Nur negative IDs (Seiten/Kommentare anderer Instanzen) - positive gehen an die normalen Endpunkte."""
    regex = "-[1-9][0-9]{0,17}"

    def convert(self, value):
        return int(value)

    def to_string(self, value):
        return str(value)


register_url_convertor("fedid", FedId())


class _Fail(Exception):
    def __init__(self, resp):
        super().__init__()
        self.resp = resp


def _unavailable():
    return _bad(t("Die andere Saga-Instanz ist gerade nicht erreichbar."), 502)


def _home_actor(c, uid, peer_id):
    """Wer fragt: oeffentlicher Name (Protokoll 2: sonst pseudonymer Handle), Anzeigename und die Gruppen, die die andere
    Instanz als Ziel genannt hat."""
    p = peer_by(c, "id", peer_id)
    pub = fed_name(c, uid, p)
    if not pub:
        raise _Fail(_bad(t("Für Seiten anderer Saga-Instanzen brauchst du einen öffentlichen Namen (Einstellungen)."), 403))
    named = {x[0] for x in c.execute("SELECT DISTINCT target FROM fed_inbound WHERE peer_id=? AND target_type='group'", (peer_id,))}
    out = {"name": pub, "display": _clean_text(display_of(c, uid) or pub, DISPLAY_MAX),
           "groups": sorted(sharing.groups_of(c, uid) & named)}
    if "rename" in caps_of(p):              # 2.5: stabiler Handle dazu (die andere Instanz fuehrt Handle und Namen zusammen)
        r = c.execute("SELECT handle FROM fed_handle WHERE uid=?", (uid,)).fetchone()
        if not r:
            with _db() as c2, c2:            # eigene Transaktion (der Aufrufer liest nur - sonst verfiele der neue Handle)
                own_handle(c2, uid)
            r = c.execute("SELECT handle FROM fed_handle WHERE uid=?", (uid,)).fetchone()
        if r:
            out["handle"] = r[0]
    return out


def _home_peer(c, peer_id):
    p = peer_by(c, "id", peer_id)
    if not enabled() or not p or p["status"] != "active":
        raise _Fail(_bad("not found", 404))
    return p


def _proxy(uid, peer_id, method, path, query="", body=None):
    """-> (peer, status, json). _Fail bei fehlender Verbindung/Namen; nicht erreichbar -> 502."""
    with _db() as c:
        p = _home_peer(c, peer_id)
        actor = _home_actor(c, uid, peer_id)
    try:
        status, d = call_json(p, method, path, query, body, actor)
    except FedError as ex:
        with _db() as c, c:
            _touch(c, p, ex)
        raise _Fail(_unavailable())
    return p, status, d


def _in_person(c, p, peer, uid, cache):
    """Person aus der Antwort einer anderen Instanz -> Anzeige hier (fremde immer mit Herkunft)."""
    u, name = p.get("username"), remote_name(p.get("name"))
    if u and is_remote(u) and _handle(u)[0]:
        n, host = _handle(u)
        if host == SELF_HOST:                               # eigene Person (z. B. ich selbst)
            if u not in cache:
                cache[u] = _local_user_by_name(c, n)
            lu, disp = cache[u]
            if lu:
                return {"name": disp, "username": lu, "me": lu == uid, "avatar": _avatar_cached(c, lu, cache)}
        pr = f"@{n}@{host}"
        return {"name": remote_label(pr, name), "username": pr, "me": bool(p.get("me")), "remote": True,
                "handle": f"{n}@{host}", "avatar": _avatar_cached(c, pr, cache)}
    return {"name": f"{name} ({peer['host']})" if name else peer["host"], "username": None, "me": False, "remote": True, "avatar": None}


def _avatar_cached(c, who, cache):
    k = ("avatar", who)
    if k not in cache:
        cache[k] = avatars.url_of(c, who)
    return cache[k]


def _in_persons(c, obj, peer, uid, cache=None):
    cache = {} if cache is None else cache
    if isinstance(obj, list):
        return [_in_persons(c, x, peer, uid, cache) for x in obj]
    if not isinstance(obj, dict):
        return obj
    if _is_person(obj):
        return _in_person(c, obj, peer, uid, cache)
    return {k: _in_persons(c, v, peer, uid, cache) for k, v in obj.items()}


_FILES_IN = re.compile(r"/files/([0-9a-f]{24})")
_LINKS_IN = re.compile(r"/notes#([1-9][0-9]{0,17})")
_FILES_OUT = re.compile(r"/api/fed/files/(-[1-9][0-9]{0,17})/([0-9a-f]{24})")
_LINKS_OUT = re.compile(r"/notes#(-?[0-9]{1,19})")


def _rw_in(s, pid, page_ref):
    """Inhalt einer fremden Seite fuer die Anzeige hier: Bilder ueber den Proxy, Seitenverweise auf negative IDs."""
    if not isinstance(s, str):
        return s
    s = _FILES_IN.sub(lambda m: f"/api/fed/files/{page_ref}/{m.group(1)}", s)
    return _LINKS_IN.sub(lambda m: f"/notes#{ref(pid, int(m.group(1)))}" if int(m.group(1)) < REMOTE_ID_MAX else "#", s)


def _rw_out(s, pid):
    """Beim Speichern zurueck: Bilder/Verweise derselben Instanz wieder in deren Form; Verweise auf hiesige oder dritte
    Seiten werden entschaerft ('#' - der Server dort macht daraus Text)."""
    if not isinstance(s, str):
        return s

    def f(m):
        p, _ = unref(int(m.group(1)))
        return f"/files/{m.group(2)}" if p == pid else "/files/0"

    def ln(m):
        v = int(m.group(1))
        if v < 0:
            p, rid = unref(v)
            if p == pid:
                return f"/notes#{rid}"
        return "#"
    return _LINKS_OUT.sub(ln, _FILES_OUT.sub(f, s))


_PIC = re.compile(r"/files/[0-9a-f]{24}")


def _safe_pic(v, icon):
    """Symbol/Titelbild einer fremden Seite: nur ein Bild dieser Seite (/files/<id>, wird zum Proxy) oder - beim Symbol -
    ein kurzes Emoji wie bei eigenen Seiten. Alles andere (fremde oder hiesige Adressen wie /logout) faellt weg."""
    s = v.strip() if isinstance(v, str) else ""
    if _PIC.fullmatch(s):
        return s
    if icon and len(s) <= 8 and not any(ch in s for ch in "<>&\"'/\\:") and not re.search(r"[\x00-\x1f\x7f]", s):
        return s
    return ""


def _clean_remote_body(s, fmt):
    """Inhalt einer fremden Seite: dieselbe Bereinigung wie beim Speichern eigener Seiten (Positivliste fuer Inline-HTML),
    Bild-/Datei-Bloecke nur mit Dateien dieser Seite (/files/<id>) - eine boesartige Instanz kann so weder HTML noch
    Adressen dieses Servers (GET auf /logout o. ae.) in die Oberflaeche bringen."""
    if not isinstance(s, str):
        return ""
    if fmt != "blocks":
        return s
    try:
        body, _ = pages.clean_blocks(s, max(len(s), 1))
        doc = json.loads(body)
        for b in _iter_blocks(doc["blocks"]):
            if b.get("type") in ("image", "attaches"):
                f = b["data"].get("file") if isinstance(b.get("data"), dict) else None
                if not isinstance(f, dict) or not _PIC.fullmatch(str(f.get("url") or "")):
                    b["data"] = dict(b.get("data") or {}, file={"url": ""})
    except (ValueError, TypeError, KeyError, RecursionError):
        return json.dumps({"blocks": []})
    return json.dumps(doc, ensure_ascii=False)


def _synced_src(body):
    """Roher Inhalt einer fremden Seite: {block_id: data.src} der synchronisierten Bloecke (vor der Bereinigung)."""
    if not isinstance(body, str) or '"synced"' not in body:
        return {}
    try:
        return {b.get("id"): b["data"]["src"] for b in _iter_blocks(json.loads(body).get("blocks") or [])
                if b.get("type") == "synced" and isinstance(b.get("data"), dict) and isinstance(b["data"].get("src"), dict)
                and isinstance(b.get("id"), str)}
    except (ValueError, AttributeError, TypeError, RecursionError):
        return {}


def _synced_in(body, pid, src_in=None):
    """Synchronisierte Bloecke einer fremden Seite: src (aus src_in je Block-ID) mit note auf negativen IDs, nur bekannte
    Felder; ohne src (Gegenstelle ohne sync1) bleibt nur die sid."""
    try:
        doc = json.loads(body)
        for b in _iter_blocks(doc.get("blocks") or []):
            if b.get("type") != "synced" or not isinstance(b.get("data"), dict):
                continue
            b["data"].pop("srev", None)               # Basis des Durchschreibens: hier wieder als src.rev
            src = (src_in or {}).get(b.get("id")) or {}
            if not isinstance(src, dict):
                src = {}
            out = {k: bool(src[k]) for k in ("original", "gone", "noaccess", "missing") if src.get(k)}
            if _ok_rid(src.get("note")):
                out["note"] = ref(pid, src["note"])
            if isinstance(src.get("block"), str) and re.fullmatch(r"[A-Za-z0-9_-]{0,40}", src["block"]):
                out["block"] = src["block"]
            if src.get("role") in ROLES or src.get("role") == "owner":
                out["role"] = "edit" if src["role"] == "owner" else src["role"]
            for k in ("rev", "refs"):
                if isinstance(src.get(k), int) and not isinstance(src.get(k), bool) and 0 <= src[k] < 2 ** 62:
                    out[k] = src[k]
            b["data"]["src"] = out
            if out.get("noaccess") or not out:
                b["data"].pop("blocks", None)
        return json.dumps(doc, ensure_ascii=False)
    except (ValueError, RecursionError, TypeError):
        return body


def _db_blocks(body, pid, inbound):
    """Eingebettete Datenbanken (Block 'database', data.db) und Schaltflaechen (Block 'buttons': Ziel target, Vorlage tpl)
    einer fremden Seite: IDs der anderen Instanz <-> negative IDs. Beim Speichern: nur IDs derselben Instanz, alles
    andere wird leer (wie entschaerfte Verweise). Die Signatur der Schaltflaeche (sig) bleibt, wie sie ist - sie gilt
    fuer die IDs dort (saga.databases.sign_buttons)."""
    if not isinstance(body, str) or ('"database"' not in body and '"buttons"' not in body):
        return body
    try:
        doc = json.loads(body)
    except ValueError:
        return body
    changed = False

    def conv(v):
        if inbound:
            return ref(pid, v) if _ok_rid(v) else None
        return unref(v)[1] if isinstance(v, int) and not isinstance(v, bool) and v < 0 and unref(v)[0] == pid else None
    try:
        for b in _iter_blocks(doc.get("blocks") or []) if isinstance(doc, dict) else []:
            if not isinstance(b.get("data"), dict):
                continue
            if b.get("type") == "database":
                v = b["data"].get("db")
                nv = conv(v)
                if nv != v:
                    b["data"]["db"] = nv
                    changed = True
            elif b.get("type") == "buttons" and isinstance(b["data"].get("buttons"), list):
                for bt in b["data"]["buttons"]:
                    if not isinstance(bt, dict):
                        continue
                    for k in ("target", "tpl"):
                        if k in bt and bt[k] is not None:
                            nv = conv(bt[k])
                            if nv != bt[k]:
                                bt[k] = nv
                                changed = True
    except ValueError:
        return body
    return json.dumps(doc, ensure_ascii=False) if changed else body


def _num(v, default=0):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) < 1e15 else default


def _typed(d):
    """Felder der Gegenstelle auf die Typen bringen, die Web und Apps erwarten (BUG-034) - kaputte Werte fallen weg."""
    tags = d.get("tags")
    d["tags"] = [_clean_text(x, 40) for x in tags[:50] if isinstance(x, str) and _clean_text(x, 40)] if isinstance(tags, list) else []
    d["kind"] = "database" if d.get("kind") == "database" else "page"
    d["icon"] = d.get("icon") if isinstance(d.get("icon"), str) else ""
    for k in ("updated", "created"):
        if k in d:
            d[k] = _num(d.get(k))
    for k in ("template", "root", "unread", "wide", "archived", "small"):
        if k in d:
            d[k] = d.get(k) is True
    if "rev" in d:
        d["rev"] = d["rev"] if _ok_rid(d.get("rev")) or d.get("rev") == 0 else 0
    if "cover_pos" in d:
        d["cover_pos"] = max(0.0, min(100.0, float(_num(d.get("cover_pos"), 50.0))))
    if "font" in d and d["font"] not in ("serif", "mono"):       # 2.5 (G9): nur bekannte Schriften, sonst Standard
        d["font"] = None
    if "props" in d and not isinstance(d.get("props"), dict):
        d["props"] = {}
    for k in ("owner", "updated_by", "created_by"):
        if k in d and not isinstance(d.get(k), dict):
            d[k] = None
    return d


SHARED_KEYS = ("id", "title", "icon", "parent_id", "updated", "tags", "pinned", "template", "kind", "role", "root", "shared", "owner", "unread")


def _body_in(c, pid, pr, uid, body, fmt, pm_in):
    """Inhalt einer fremden Seite fuer die Anzeige hier (GET, ?since-Delta, Antwort auf Zusammenfuehren): bereinigen,
    Bilder/Verweise/Datenbanken umschreiben, Erwaehnungen auf Kennungen von hier (P6-5), Herkunft synchronisierter
    Kopien auf negative IDs (P6-6). -> (body, people der Erwaehnungen oder None)."""
    src_in = _synced_src(body) if fmt == "blocks" else {}      # 2.5 (P6-6): data.src entfernt die Bereinigung
    body = _db_blocks(_rw_in(_clean_remote_body(body, fmt), pid, pr), pid, True)
    pm = None
    if fmt == "blocks" and "hk-mention" in (body or ""):        # 2.5 (P6-5): Erwaehnungen -> Kennungen von hier
        from saga import mentions
        body = mentions.in_body(c, body, SELF_HOST, lambda c_, n: _local_user_by_name(c_, n)[0])
        refs = list(mentions.refs_in(body))
        if refs:
            pm = mentions.remote_people(c, refs, pm_in, uid)
    if fmt == "blocks" and '"synced"' in (body or ""):          # 2.5 (P6-6): Herkunft der Kopien auf negative IDs
        body = _synced_in(body, pid, src_in)
    return body, pm


def _note_in(c, peer, d, uid):
    pid = peer["id"]
    rid = d.get("id")
    if not _ok_rid(rid):
        raise _Fail(_unavailable())
    pr = ref(pid, rid)
    d = _typed(_in_persons(c, d, peer, uid))
    d["id"] = pr
    d["parent_id"] = ref(pid, d["parent_id"]) if _ok_rid(d.get("parent_id")) else None
    d["title"] = _clean_text(d.get("title"), 500)
    d["format"] = "blocks" if d.get("format") == "blocks" else "text"
    # Inhalt der anderen Instanz wie eigenen behandeln: erst bereinigen (IDs noch in deren Form), dann umschreiben
    pm_in = d.pop("people", None)
    d["body"], pm = _body_in(c, pid, pr, uid, d.get("body"), d["format"], pm_in)
    if pm is not None:
        d["people"] = pm
    if pm_in is not None and "people" not in d:
        d["people"] = pm_in
    d["icon"] = _rw_in(_safe_pic(d.get("icon"), True), pid, pr)
    d["cover"] = _rw_in(_safe_pic(d.get("cover"), False), pid, pr)
    if d.get("role") not in ROLES:
        d["role"] = "view"
    d.update(pinned=False, published=False, public_url=None, published_via=None, shared=True, locked=bool(d.get("locked")),
             remote=remote_info(peer))
    if isinstance(d.get("props"), dict):     # Relationen (Listen von Seiten-IDs) auf negative IDs
        d["props"] = fed_dbx.props_in(c, props_in(pid, d["props"]), pr)      # 2.5 (dbx1): Personen/Dateien
        ment_pm = d.get("people") if d.get("format") == "blocks" and "hk-mention" in (d.get("body") or "") else None
        if isinstance(d.get("people"), dict) or any(isinstance(v, list) and v and isinstance(v[0], str) and v[0][:1] in "~@"
                                                    for v in d["props"].values()):
            d["people"] = fed_dbx.people_in(c, peer, uid, d.get("people"), [d["props"]])
        if isinstance(ment_pm, dict):         # 2.5 (P6-5): Woerterbuch der Erwaehnungen (oben) behalten
            d["people"] = dict(ment_pm, **(d.get("people") or {}))
            if not d["people"]:
                d.pop("people")
    elif not (d.get("format") == "blocks" and "hk-mention" in (d.get("body") or "")):
        d.pop("people", None)
    if "fx_err" in d:                        # 2.5 (formula1): Fehler der Formeln dieser Zeile, nur {pid: code}
        d["fx_err"] = fx_err_in(d.get("fx_err"))
        if not d["fx_err"]:
            d.pop("fx_err")
    if d.get("kind") == "database" and feature(peer, "db"):      # 2.3: Datenbanken anderer Instanzen oeffnen/bearbeiten
        d["schema"] = schema_in(pid, d.get("schema"))
    elif d.get("kind") == "database":        # aeltere Gegenstelle (Protokoll 1): nur ein Hinweis
        d.pop("schema", None)
        d.update(kind="page", role="view", format="blocks", comment_counts={}, comments_open=0, comments_unread=0,
                 body=json.dumps({"blocks": [{"id": "fed-db", "type": "paragraph", "data": {"text": _esc_text(
                     t("Diese Datenbank liegt auf {host}. Datenbanken anderer Saga-Instanzen lassen sich hier noch nicht öffnen.",
                       host=peer["host"]))}}]}, ensure_ascii=False))
    return d


PAGE_CAPS = ("db", "subpages", "upload", "versions", "export", "presence", "live", "chatgroups", "tplbtn", "widgets1", "rowtpl", "csv",
             "formula1", "dbx1", "widgets2", "merge1", "presence2", "ment1", "sync1")


def remote_info(peer):
    """Herkunft einer fremden Seite fuer Web/Apps: {peer, host, name, caps} - caps: was mit diesem Server geht (2.3)."""
    return {"peer": peer["id"], "host": peer["host"], "name": peer["name"], "caps": [x for x in PAGE_CAPS if feature(peer, x)]}


def props_in(pid, props):
    """Werte einer Zeile der anderen Instanz: Relationen (Listen von Seiten-IDs) auf negative IDs, sonst unveraendert."""
    out = {}
    for k, v in (props or {}).items():
        if not isinstance(k, str) or len(k) > 40:
            continue
        if isinstance(v, list) and v and all(isinstance(x, int) and not isinstance(x, bool) for x in v):
            v = [ref(pid, x) for x in v if _ok_rid(x)]
        out[k] = v
    return out


_FXCODE = re.compile(r"[a-z]{1,16}")


def fx_err_in(v):
    """fx_err einer Zeile der anderen Instanz: {eigenschaft: code} - nur kurze Kennungen."""
    if not isinstance(v, dict):
        return {}
    return {k: c for k, c in list(v.items())[:40] if isinstance(k, str) and len(k) <= 40 and isinstance(c, str) and _FXCODE.fullmatch(c)}


def props_out(pid, props):
    """Beim Speichern zurueck: negative IDs derselben Instanz -> deren IDs; andere Verweise fallen weg."""
    if not isinstance(props, dict):
        return props
    out = {}
    for k, v in props.items():
        if isinstance(v, list) and v and all(isinstance(x, int) and not isinstance(x, bool) for x in v):
            v = [unref(x)[1] for x in v if x < 0 and unref(x)[0] == pid]
        out[k] = v
    return out


def schema_in(pid, schema):
    """Schema einer Datenbank der anderen Instanz: Ziel-Datenbanken von Relationen auf negative IDs."""
    if not isinstance(schema, dict):
        return {"props": []}
    props = []
    for p in schema.get("props") or []:
        if isinstance(p, dict):
            p = dict(p)
            if p.get("type") == "relation":
                p["target"] = ref(pid, p["target"]) if _ok_rid(p.get("target")) else 0
            props.append(p)
    out = dict(schema, props=props)
    if "default_tpl" in out:              # fix27: Standardvorlage als ID dieser Instanz (vorher roh durchgereicht)
        out["default_tpl"] = ref(pid, out["default_tpl"]) if _ok_rid(out.get("default_tpl")) else None
    return out


def schema_out(pid, schema):
    if not isinstance(schema, dict) or not isinstance(schema.get("props"), list):
        return schema
    props = []
    for p in schema["props"]:
        if isinstance(p, dict) and p.get("type") == "relation":
            tg = p.get("target")
            try:
                tg = int(tg)
            except (TypeError, ValueError):
                tg = 0
            p = dict(p, target=unref(tg)[1] if tg < 0 and unref(tg)[0] == pid else 0)
        props.append(p)
    out = dict(schema, props=props)
    out.pop("default_tpl", None)          # setzt nur der Eigentuemer (bleibt beim Speichern erhalten, saga.pages)
    return out


def _esc_text(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _cref(pid, v):
    return ref(pid, v) if _ok_rid(v) else None


def _tokens_in(c, body):
    """Kommentar einer anderen Instanz: Token "<@@name@host>" -> Kennung hier (eigene Personen als Handle)."""
    from saga import mentions
    if not isinstance(body, str) or "<@" not in body:
        return body

    def rep(m):
        r = m.group(1)
        if not r.startswith("@"):
            return "@?"                              # fremde Handles gelten nur dort
        name, host = _handle(r)
        if host == SELF_HOST:
            u = _local_user_by_name(c, name)[0] if name else None
            return f"<@{local_ref(u)}>" if u else "@?"
        return f"<@{people.canon_remote(c, r)}>"
    return mentions._TOKEN.sub(rep, body)


def _comments_in(c, peer, d, uid):
    pid = peer["id"]
    pm_in = d.pop("people", None) if isinstance(d, dict) else None
    d = _in_persons(c, d, peer, uid)
    refs = []
    for th in d.get("threads") or []:
        for cm in th.get("comments") or []:
            if isinstance(cm.get("body"), str) and "<@" in cm["body"]:
                cm["body"] = _tokens_in(c, cm["body"])
                from saga import mentions
                refs += mentions._TOKEN.findall(cm["body"])
    if refs:
        from saga import mentions
        d["people"] = mentions.remote_people(c, list(dict.fromkeys(refs)), pm_in, uid)
    for th in d.get("threads") or []:
        th["id"] = _cref(pid, th.get("id"))
        for cm in th.get("comments") or []:
            cm["id"], cm["thread_id"] = _cref(pid, cm.get("id")), _cref(pid, cm.get("thread_id"))
    d["is_owner"] = False
    return d


def _run(fn, *args):
    """Proxy-Arbeit (im Thread): _Fail in die Fehlerantwort umwandeln."""
    try:
        return fn(*args)
    except _Fail as f:
        return f.resp


def _json(status, d):
    return JSONResponse(d, status_code=status, headers={"Cache-Control": "no-store"})


def _pass_err(peer, status, d):
    """Fehler der anderen Instanz durchreichen (Text kommt in deren Sprache - besser als nichts)."""
    if status in (401, 407) or not 400 <= status < 500:
        # 401/407 hiesse fuer die Apps "neu anmelden" (Sitzung HIER ist aber gueltig), Weiterleitungen/5xx sind keine
        # verwertbare Antwort - nie Fremdtext durchreichen (BUG-036)
        return _unavailable()
    out = {"error": _clean_text((d or {}).get("error"), 300) or f"HTTP {status}"} if isinstance(d, dict) else {"error": f"HTTP {status}"}
    for k in ("rev", "by"):
        if isinstance(d, dict) and k in d:
            out[k] = d[k] if k == "rev" and isinstance(d[k], int) else _clean_text(d[k], 200) if d[k] else None
    return _json(status if 400 <= status < 500 else 502, out)


def _w_note_get(uid, nid, since=None):
    pid, rid = unref(nid)
    with _db() as c:
        hp = _home_peer(c, pid)
        v2, m1 = feature(hp, "db"), feature(hp, "merge1")
    p, status, d = _proxy(uid, pid, "GET", f"/fed/v2/notes/{rid}" if v2 else f"/fed/v1/notes/{rid}",
                          f"since={since}" if since is not None and m1 else "")
    if status != 200 or not isinstance(d, dict):
        return _pass_err(p, status, d)
    delta = d.pop("delta", None) if m1 and since is not None and "body" not in d else None
    if delta is not None:
        d["body"] = json.dumps({"blocks": []})
    pm_in = d.get("people") if isinstance(d.get("people"), dict) else None
    with _db() as c:
        out = _note_in(c, p, d, uid)
        if delta is not None:
            dl = _delta_in(delta, p["id"], nid, since, c, uid, pm_in)
    if delta is not None:
        if dl is None:                          # unbrauchbar: ganzer Inhalt wie bisher
            return _w_note_get(uid, nid)
        out.pop("body", None)
        out["delta"] = dl.pop("delta")
        if dl.get("people"):                    # 2.5 (P6-5): Namen der Erwaehnungen in den geaenderten Bloecken
            out["people"] = dict(out.get("people") or {}, **dl["people"])
    return _json(200, out)


def _delta_in(delta, pid, nid, since, c=None, uid=None, pm_in=None):
    """?since-Antwort des Eigentuemers: vollstaendige Bloecke wie beim Lesen bereinigt und umgeschrieben (wie _body_in),
    Platzhalter {"id"} unveraendert. -> {"delta": {"from", "blocks"}, "people"} oder None."""
    if not isinstance(delta, dict) or delta.get("from") != since or not isinstance(delta.get("blocks"), list):
        return None
    full = [b for b in delta["blocks"] if isinstance(b, dict) and set(b) != {"id"}]
    try:
        body, pm = _body_in(c, pid, nid, uid, json.dumps({"blocks": full}), "blocks", pm_in)
        doc = json.loads(body)
    except (ValueError, TypeError):
        return None
    by = {b.get("id"): b for b in doc.get("blocks") or []}
    out = []
    for b in delta["blocks"][:5000]:
        if not isinstance(b, dict):
            continue
        bid = b.get("id")
        if not (isinstance(bid, str) and BLOCK_ID_RE.fullmatch(bid)):
            return None
        if set(b) == {"id"}:
            out.append({"id": bid})
        elif bid in by:
            out.append(by[bid])
    return {"delta": {"from": since, "blocks": out}, "people": pm}


def _pic_out(v, pid):
    """Symbol/Titelbild beim Speichern: Bild derselben Instanz (/api/fed/files/-id/fid) -> /files/fid, Emoji bleibt."""
    if not isinstance(v, str):
        return v
    m = _FILES_OUT.fullmatch(v.strip())
    if m:
        p, _ = unref(int(m.group(1)))
        return f"/files/{m.group(2)}" if p == pid else ""
    return "" if v.strip().startswith("/") else v


def _w_note_patch(uid, nid, b):
    pid, rid = unref(nid)
    with _db() as c:
        v2 = feature(_home_peer(c, pid), "db")
    with _db() as c:
        keys = edit_keys(_home_peer(c, pid), v2)
    mf = merge_fields(b) if "merge" in keys else {}        # 2.5 (merge1): ohne die Faehigkeit beim Eigentuemer -> 409 wie bisher
    b = dict({k: v for k, v in b.items() if k in keys and k not in MERGE_KEYS}, **mf)
    if not b:
        return _bad(t("Bei Seiten anderer Saga-Instanzen lassen sich nur Titel und Inhalt ändern."), 403)
    if "body" in b:
        b["body"] = _db_blocks(_rw_out(b["body"], pid), pid, False)
        if isinstance(b["body"], str) and "hk-mention" in b["body"]:      # 2.5 (P6-5): lokale Handles -> '@name@hier'
            from saga import mentions
            with _db() as c:
                hp = _home_peer(c, pid)
                b["body"] = mentions.home_out(c, b["body"], lambda u: (lambda pub: f"@{pub}@{SELF_HOST}" if pub else None)(fed_name(c, u, hp)))
    for k in ("icon", "cover"):
        if k in b and b[k]:
            b[k] = _pic_out(b[k], pid)
    if "props" in b:
        b["props"] = fed_dbx.props_out(pid, props_out(pid, b["props"]))     # 2.5 (dbx1): Personen/Dateien zurueck
    if "schema" in b:
        b["schema"] = schema_out(pid, b["schema"])
    p, status, d = _proxy(uid, pid, "PATCH", f"/fed/v2/notes/{rid}" if v2 else f"/fed/v1/notes/{rid}", body=b)
    if status != 200 or not isinstance(d, dict):
        return _pass_err(p, status, d)
    out = {k: d.get(k) for k in ("ok", "updated", "rev", "srev") if k in d or k != "srev"}
    if isinstance(d.get("synced"), dict):        # 2.5 (P6-6): Durchschreiben je sid und neuer Stand der Originale
        out["synced"] = {k: v for k, v in list(d["synced"].items())[:200] if isinstance(k, str) and isinstance(v, str) and len(v) <= 10}
        sr = d.get("synced_rev") if isinstance(d.get("synced_rev"), dict) else {}
        out["synced_rev"] = {k: v for k, v in list(sr.items())[:200] if k in out["synced"] and isinstance(v, int) and not isinstance(v, bool)}
    if isinstance(d.get("merged"), dict) and "merge" in b:     # 2.5 (merge1): zusammengefuehrt - Stand wie GET (umgeschrieben)
        with _db() as c:
            out.update(_merged_in(c, p, d, uid, nid))
    if isinstance(d.get("calc"), dict):          # 2.5 (formula1): Formel-/Rollup-Werte der Zeile nach dem Speichern
        out["calc"] = props_in(pid, {k: v for k, v in list(d["calc"].items())[:40] if isinstance(k, str) and len(k) <= 40})
        out["fx_err"] = fx_err_in(d.get("fx_err"))
    if isinstance(d.get("values"), dict):        # 2.5 (dbx1): Personen/Dateien der Zeile nach dem Speichern
        with _db() as c:
            vals = fed_dbx.props_in(c, {k: v for k, v in list(d["values"].items())[:40] if isinstance(k, str) and len(k) <= 40}, nid)
            out["values"] = vals
            out["people"] = fed_dbx.people_in(c, p, uid, d.get("people"), [vals])
    return _json(200, out)


def _merged_in(c, p, d, uid, nid):
    """Antwort des Eigentuemers auf ein zusammengefuehrtes Speichern -> wie lokal: merged (nur Block-IDs, Zahlen, Schalter)
    und Inhalt/Titel/Format so, wie GET /api/notes/{-id} sie zeigt (bereinigt, Bilder/Verweise/Datenbanken umgeschrieben)."""
    pid, m = p["id"], d["merged"]

    def ids(v):
        return [x for x in v[:500] if isinstance(x, str) and BLOCK_ID_RE.fullmatch(x)] if isinstance(v, list) else []
    mg = {k: ids(m.get(k)) for k in ("conflicts", "restored", "lost")}
    mg["from"] = m["from"] if isinstance(m.get("from"), int) and not isinstance(m.get("from"), bool) else None
    if m.get("title_lost") is True:
        mg["title_lost"] = True
    out = {"merged": mg}
    if isinstance(d.get("body"), str):
        fmt = "blocks" if d.get("format") == "blocks" else "text"
        out["format"] = fmt
        out["body"], pm = _body_in(c, pid, nid, uid, d["body"], fmt, d.get("people") if isinstance(d.get("people"), dict) else None)
        if pm:
            out["people"] = pm
    if "title" in d:
        out["title"] = _clean_text(d.get("title"), 500)
    return out


def _w_comments(uid, nid, resolved):
    pid, rid = unref(nid)
    p, status, d = _proxy(uid, pid, "GET", f"/fed/v1/notes/{rid}/comments", "resolved=1" if resolved else "")
    if status != 200 or not isinstance(d, dict):
        return _pass_err(p, status, d)
    with _db() as c:
        return _json(200, _comments_in(c, p, d, uid))


def _w_comment_add(uid, nid, b):
    pid, rid = unref(nid)
    out = {"body": b.get("body"), "block_id": b.get("block_id")}
    if b.get("thread_id") is not None:
        try:
            tp, trid = unref(int(b["thread_id"]))
        except (TypeError, ValueError, OverflowError):
            return _bad(t("ungültige Angabe"))
        if tp != pid or int(b["thread_id"]) >= 0:
            return _bad("not found", 404)
        out["thread_id"] = trid
    if isinstance(out["body"], str) and "<@" in out["body"]:             # 2.5 (P6-5): Erwaehnungen als Prinzipal
        out["body"] = _tokens_out(out["body"], pid)
    p, status, d = _proxy(uid, pid, "POST", f"/fed/v1/notes/{rid}/comments", body=out)
    if status != 200 or not isinstance(d, dict):
        return _pass_err(p, status, d)
    with _db() as c:
        d = _in_persons(c, d, p, uid)
    cm = d.get("comment") or {}
    cm["id"], cm["thread_id"] = _cref(pid, cm.get("id")), _cref(pid, cm.get("thread_id"))
    return _json(200, {"comment": cm, "thread_id": _cref(pid, d.get("thread_id"))})


def _tokens_out(body, pid):
    """Kommentar an eine andere Instanz: lokale Handles -> "<@@name@hier>" (ohne oeffentlichen Namen: "@Name")."""
    from saga import mentions
    with _db() as c:
        hp = _home_peer(c, pid)

        def rep(m):
            r = m.group(1)
            if HANDLE_RE.fullmatch(r):
                u = resolve_ref(c, r)
                pub = fed_name(c, u, hp) if u else None
                if pub and feature(hp, "ment1"):
                    return f"<@@{pub}@{SELF_HOST}>"
                return "@" + (mentions.people_map(c, [r], None).get(r) or {}).get("name", "?")
            return m.group(0) if feature(hp, "ment1") else "@" + r.lstrip("@").split("@")[0]
        return mentions._TOKEN.sub(rep, body)


def _w_comment(uid, cid, method, suffix="", body=None, query=""):
    pid, rcid = unref(cid)
    if isinstance(body, dict) and isinstance(body.get("body"), str) and "<@" in body["body"]:
        body = dict(body, body=_tokens_out(body["body"], pid))
    p, status, d = _proxy(uid, pid, method, f"/fed/v1/comments/{rcid}{suffix}", query, body)
    if status != 200 or not isinstance(d, dict):
        return _pass_err(p, status, d)
    with _db() as c:
        d = _in_persons(c, d, p, uid)
    if isinstance(d.get("comment"), dict):
        d["comment"]["id"], d["comment"]["thread_id"] = _cref(pid, d["comment"].get("id")), _cref(pid, d["comment"].get("thread_id"))
    if "thread_id" in d:
        d["thread_id"] = _cref(pid, d.get("thread_id"))
    return _json(200, d)


def _uid_or_401(request):
    uid = _api_guard(request)
    return uid, (None if uid else _bad("unauthorized", 401))


@router.get("/api/notes/{nid:fedid}")
def home_note_get(nid: int, request: Request):
    uid, err = _uid_or_401(request)
    return err or _run(_w_note_get, uid, nid, pages.since_arg(request.query_params.get("since")))


@router.patch("/api/notes/{nid:fedid}")
async def home_note_patch(nid: int, request: Request):
    uid, err = _uid_or_401(request)
    if err:
        return err
    b = await _body(request)
    if b is None:
        return _bad("bad request")
    return await run_in_threadpool(_run, _w_note_patch, uid, nid, b)


@router.get("/api/notes/{nid:fedid}/comments")
def home_comments(nid: int, request: Request, resolved: int = 0):
    uid, err = _uid_or_401(request)
    return err or _run(_w_comments, uid, nid, resolved)


@router.post("/api/notes/{nid:fedid}/comments")
async def home_comment_add(nid: int, request: Request):
    uid, err = _uid_or_401(request)
    if err:
        return err
    b = await _body(request)
    if b is None:
        return _bad(t("ungültige Anfrage"))
    return await run_in_threadpool(_run, _w_comment_add, uid, nid, b)


@router.patch("/api/comments/{cid:fedid}")
async def home_comment_edit(cid: int, request: Request):
    uid, err = _uid_or_401(request)
    if err:
        return err
    b = await _body(request) or {}
    return await run_in_threadpool(_run, _w_comment, uid, cid, "PATCH", "", {"body": b.get("body")})


@router.delete("/api/comments/{cid:fedid}")
def home_comment_delete(cid: int, request: Request, all: int = 0):
    uid, err = _uid_or_401(request)
    return err or _run(_w_comment, uid, cid, "DELETE", "", None, "all=1" if all else "")


@router.post("/api/comments/{cid:fedid}/resolve")
async def home_comment_resolve(cid: int, request: Request):
    uid, err = _uid_or_401(request)
    if err:
        return err
    b = await _body(request) or {}
    return await run_in_threadpool(_run, _w_comment, uid, cid, "POST", "/resolve", {"resolved": b.get("resolved", True) is not False})


@router.post("/api/comments/{cid:fedid}/seen")
def home_comment_seen(cid: int, request: Request):
    uid, err = _uid_or_401(request)
    return err or _run(_w_comment, uid, cid, "POST", "/seen", {})


_IMG_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")


@router.get("/api/fed/files/{nid:fedid}/{fid}")
def home_file(nid: int, fid: str, request: Request):
    """Bild/Datei einer fremden Seite ueber den Eigentuemer-Server (Sitzung reicht - <img> sendet keine Kopfzeilen)."""
    uid = _session_user(request)
    if not uid or not _FID.fullmatch(fid):
        return _bad("not found", 404)
    pid, rid = unref(nid)
    q = f"note={rid}"
    w = _thumb_w(request)
    if w:
        q += f"&w={w}"
    try:
        with _db() as c:
            p = _home_peer(c, pid)
            actor = _home_actor(c, uid, pid)
        status, rh, data = call(p, "GET", f"/fed/v1/files/{fid}", q, None, actor, limit=FILE_MAX + 1024)
    except _Fail as f:
        return f.resp
    except FedError:
        return _unavailable()
    if status != 200:
        return _bad("not found", 404)
    mime = (rh.get("content-type") or "").split(";")[0].strip().lower()
    h = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox", "Referrer-Policy": "no-referrer",
         "Cache-Control": "private, max-age=300"}
    if mime in _IMG_TYPES:
        h["Content-Disposition"] = "inline"
        return Response(data, media_type=mime, headers=h)
    disp = rh.get("content-disposition") or ""
    m = re.search(r'filename="([A-Za-z0-9 ._()\-]{1,200})"', disp)
    h["Content-Disposition"] = f'attachment; filename="{m.group(1) if m else "datei"}"'
    return Response(data, media_type="application/octet-stream", headers=h)


def _shared_from(uid, p, actor):
    try:
        status, d = call_json(p, "GET", "/fed/v1/shared", actor=actor, timeout=LIST_TIMEOUT)
    except FedError as ex:
        return p, None, str(ex)
    return p, d if status == 200 and isinstance(d, list) else None, None if status == 200 else f"HTTP {status}"


@router.get("/api/fed/shared")
def home_shared(request: Request):
    """"Mit mir geteilt" von anderen Instanzen (parallel, je Instanz hoechstens LIST_TIMEOUT s)."""
    uid, err = _uid_or_401(request)
    if err:
        return err
    if not enabled():
        return _json(200, {"items": [], "unreachable": []})
    with _db() as c:
        gs = sorted(sharing.groups_of(c, uid))
        sql = "SELECT DISTINCT peer_id FROM fed_inbound WHERE (target_type='user' AND target=?)" + \
              (f" OR (target_type='group' AND target IN ({','.join('?' * len(gs))}))" if gs else "")
        pids = [r[0] for r in c.execute(sql, (uid, *gs))]
        jobs = []
        for pid in pids:
            p = peer_by(c, "id", pid)
            if p and p["status"] == "active" and fed_name(c, uid, p):
                jobs.append((p, _home_actor(c, uid, pid)))
    items, bad = [], []
    if jobs:
        with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as ex:
            res = list(ex.map(lambda j: _shared_from(uid, *j), jobs))
        with _db() as c:
            for p, lst, e in res:
                if lst is None:
                    bad.append(p["host"])
                    continue
                for d in lst[:2000]:
                    if not isinstance(d, dict) or not _ok_rid(d.get("id")):
                        continue
                    d = _typed(_in_persons(c, {k: d[k] for k in SHARED_KEYS if k in d}, p, uid))   # nur bekannte Felder (BUG-034)
                    d["id"] = ref(p["id"], d["id"])
                    d["parent_id"] = ref(p["id"], d["parent_id"]) if _ok_rid(d.get("parent_id")) and not d.get("root") else None
                    d["icon"] = _rw_in(_safe_pic(d.get("icon"), True), p["id"], d["id"])
                    d["title"] = _clean_text(d.get("title"), 500)
                    if d.get("role") not in ROLES:
                        d["role"] = "view"
                    d.update(shared=True, pinned=False, remote=remote_info(p))
                    items.append(d)
    return _json(200, {"items": items, "unreachable": bad})


# =====================================================================================================
# Teilen an Personen/Gruppen anderer Instanzen (Haken in saga.sharing und saga.notify)
# =====================================================================================================
def _parse_target(typ, tg):
    """'@bert@b.example' / 'bert@b.example' (Person) bzw. 'team@b.example' (Gruppe) -> (name, host) oder (None, None)."""
    name, _, host = str(tg or "").strip().lower().lstrip("@").rpartition("@")
    host = fedsig.authority(host)
    if not name or not _HOST_RE.fullmatch(host or "-"):
        return None, None
    if typ == "remote" and not is_name(name):
        return None, None
    if typ == "remote_group" and auth.norm_group(name) != name:
        return None, None
    return name, host


def _lookup(p, name, kind):
    """Person/Gruppe bei der anderen Instanz nachschlagen -> Anzeigename oder None (gibt es nicht). FedError."""
    q = "resource=" + quote(f"acct:{name}@{p['host']}", safe="") + ("&kind=group" if kind == "group" else "")
    status, d = call_json(p, "GET", "/.well-known/webfinger", q, timeout=6)
    if status == 404:
        return None
    if status != 200 or not isinstance(d, dict) or d.get("kind") != kind:
        raise FedError(f"HTTP {status}", status)
    return _clean_text(d.get("name"), DISPLAY_MAX) or name


def remote_target(uid, typ, tg, nid):
    """Fuer PUT /api/notes/{nid}/shares -> (ziel, fehlertext, status). Netzwerk - ausserhalb von Transaktionen."""
    name, host = _parse_target(typ, tg)
    if not name:
        return None, t("Adresse bitte als name@server angeben."), 400
    with _db() as c:
        p = peer_by(c, "host", host)
        if not fed_name(c, uid, p if host != SELF_HOST else None) and host != SELF_HOST:
            return None, t("Zum Teilen mit anderen Saga-Instanzen brauchst du einen öffentlichen Namen (Einstellungen)."), 400
        if host == SELF_HOST:
            return None, t("Das ist dieser Server – bitte ohne @server teilen."), 400
        if not p or p["status"] != "active":
            return None, t("Mit {host} ist dieser Server nicht verbunden.", host=host), 400
        if PSEUDO_RE.fullmatch(name) and "handles" not in caps_of(p):
            return None, t("Diese Person gibt es auf {host} nicht (oder sie hat keinen öffentlichen Namen).", host=host), 400
        target = f"@{name}@{host}" if typ == "remote" else f"{name}@{host}"
        if typ == "remote" and PSEUDO_RE.fullmatch(name):
            # 2.5 (W02): Handle einer Person, die hier schon mit ihrem Namen bekannt ist -> unter diesem Namen teilen (so fragt
            # ihr Server an). Unbekannt: unter dem Handle; die erste Anfrage der Person fuehrt beides zusammen (_alias_seen).
            target = alias_principal(c, p, name) or target
        if c.execute("SELECT 1 FROM note_share WHERE note_id=? AND target_type=? AND target=?", (nid, typ, target)).fetchone():
            return target, None, 200                       # nur Rolle aendern: keine Nachfrage noetig
    try:
        disp = _lookup(p, name, "user" if typ == "remote" else "group")
    except FedError as ex:
        with _db() as c, c:
            _touch(c, p, ex)
        return None, t("Die andere Saga-Instanz ist gerade nicht erreichbar."), 502
    if disp is None:
        return None, (t("Diese Person gibt es auf {host} nicht (oder sie hat keinen öffentlichen Namen).", host=host) if typ == "remote"
                      else t("Diese Gruppe gibt es auf {host} nicht.", host=host)), 400
    if typ == "remote":
        with _db() as c, c:
            if disp != name or not c.execute("SELECT 1 FROM fed_person WHERE principal=?", (target,)).fetchone():
                _person_upsert(c, target, p["id"], disp)     # (Handle ohne Adressbuch-Eintrag: bekannten Namen nicht ueberschreiben)
    return target, None, 200


def remote_canon(c, target):
    """'@~handle@host' einer Person, die hier unter ihrem Namen gefuehrt wird -> '@name@host'; sonst unveraendert."""
    name, host = _parse_target("remote", target)
    if name and PSEUDO_RE.fullmatch(name):
        p = peer_by(c, "host", host)
        if p:
            return alias_principal(c, p, name) or f"@{name}@{host}"
    return target


def remote_search(uid, q):
    """Personensuche: genaue Adresse name@host einer verbundenen Instanz -> (treffer, hinweis). Treffer: Person
    und/oder Gruppe; hinweis (nur ohne Treffer): warum nichts gefunden wurde - z. B. E-Mail statt oeffentlichem Namen."""
    out, hint, host = [], None, None
    for typ, kind in (("remote", "user"), ("remote_group", "group")):
        name, host = _parse_target(typ, q)
        if not name:
            continue
        if host == SELF_HOST:
            return [], t("Das ist dieser Server – bitte ohne @server teilen.")
        with _db() as c:
            p = peer_by(c, "host", host)
        if not p or p["status"] != "active":
            return [], (t("Mit {host} ist dieser Server nicht verbunden.", host=host) + " "
                        + t("E-Mail-Adressen durchsucht Saga nicht – suche nach Name oder Benutzername."))
        try:
            disp = _lookup(p, name, kind)
        except FedError:
            return out, (None if out else t("Die andere Saga-Instanz ist gerade nicht erreichbar."))
        if disp is None:
            if typ == "remote":
                hint = (t("Diese Person gibt es auf {host} nicht (oder sie hat keinen öffentlichen Namen).", host=host) + " "
                        + t("Gemeint ist der öffentliche Name der Person, nicht ihre E-Mail-Adresse."))
            continue
        target = f"@{name}@{host}" if typ == "remote" else f"{name}@{host}"
        if typ == "remote":
            with _db() as c, c:
                _person_upsert(c, target, p["id"], disp)
        out.append({"type": typ, "target": target, "name": remote_label(target if typ == "remote" else "@" + target, disp if typ == "remote" else ""),
                    "host": host})
    if not out and hint is None and re.fullmatch(r"@?[^@\s]+@[^@\s]+", q or ""):   # z. B. vorname.name@firma.de
        hint = t("Gemeint ist der öffentliche Name der Person, nicht ihre E-Mail-Adresse.")
    return out, (None if out else hint)


def fed_render(c, uid, fed):
    """Benachrichtigung zu einer Seite einer anderen Instanz: Titel nur, solange die Verbindung besteht und von dort noch
    etwas mit mir (oder meinen Gruppen) geteilt ist."""
    try:
        pid, rid = int(fed.get("peer")), int(fed.get("note"))
    except (TypeError, ValueError):
        return False, None, None
    p = peer_by(c, "id", pid)
    if not enabled() or not p or p["status"] != "active" or not _ok_rid(rid):
        return False, None, None
    gs = sorted(sharing.groups_of(c, uid))
    sql = "SELECT 1 FROM fed_inbound WHERE peer_id=? AND ((target_type='user' AND target=?)" + \
          (f" OR (target_type='group' AND target IN ({','.join('?' * len(gs))}))" if gs else "") + ")"
    # Freigabe-Benachrichtigung: genau diese Seite muss noch geteilt sein (BUG-037). Kommentare koennen auch an
    # Unterseiten haengen (deren IDs kennen wir nicht) - dort reicht irgendeine Freigabe dieser Instanz.
    exact = c.execute(sql + " AND note_id=? LIMIT 1", (pid, uid, *gs, rid)).fetchone() or \
        (_ok_rid(fed.get("db")) and c.execute(sql + " AND note_id=? LIMIT 1", (pid, uid, *gs, fed["db"])).fetchone())   # 2.5: Zeile (db.assigned)
    if not exact and (not fed.get("comment") or not c.execute(sql + " LIMIT 1", (pid, uid, *gs)).fetchone()):
        return False, None, None
    r = ref(pid, rid)
    block = fed.get("block") if isinstance(fed.get("block"), str) and re.fullmatch(r"[A-Za-z0-9_-]{1,40}", fed.get("block")) else ""
    return True, {"id": r, "title": _clean_text(fed.get("title"), 300), "icon": "", "host": p["host"]}, f"/notes#{r}" + (f":{block}" if block else "")


def _fed_local(c, principal):
    """saga.people.FED_LOCAL: '@name@host' -> (gehoert zu dieser Instanz, lokaler Benutzername | None)."""
    name, host = _handle(principal)
    if not name or host != SELF_HOST:
        return False, None
    return True, _local_user_by_name(c, name)[0]


people.FED_LOCAL = _fed_local
fed_dbx.install(sys.modules[__name__])


def _mention_ext(c, uid, p=None):
    """saga.mentions.EXT_REF: lokales Konto -> '@name@hier' fuer Instanz p (oeffentlicher Name bzw. Handle) oder None."""
    pub = fed_name(c, uid, p) if uid else None
    return f"@{pub}@{SELF_HOST}" if pub else None


from saga import mentions as _mentions  # noqa: E402 - saga.mentions kennt die Foederation nicht (keine Zyklen)
_mentions.EXT_REF = _mention_ext
sharing.REMOTE_TARGET = remote_target
sharing.REMOTE_CANON = remote_canon
sharing.REMOTE_SEARCH = remote_search
sharing.FED_SCOPE_OK = scope_ok
notify.FED_RENDER = fed_render


# =====================================================================================================
# Admin-Bereich (admin.py): Einladen, Verbinden, Bestaetigen, Trennen
# =====================================================================================================
def set_enabled(on):
    auth.save_setting("fed_enabled", bool(on))
    if on:
        own_key()


def admin_state():
    with _db() as c:
        ps = peers(c)
        logs = c.execute("SELECT ts, host, event, detail FROM fed_log ORDER BY id DESC LIMIT 200").fetchall()
        invites = c.execute("SELECT id, created, expires FROM fed_invite WHERE used_at IS NULL AND expires>? ORDER BY id DESC",
                            (time.time(),)).fetchall()
        counts = {r[0]: r[1] for r in c.execute(
            "SELECT p.id, (SELECT COUNT(*) FROM note_share s WHERE s.target_type IN ('remote','remote_group') AND right(s.target, length(p.host)+1)='@'||p.host) "
            "FROM fed_peer p")}
        inbound = {r[0]: r[1] for r in c.execute("SELECT peer_id, COUNT(*) FROM fed_inbound GROUP BY peer_id")}
        queued = {r[0]: r[1] for r in c.execute("SELECT peer_id, COUNT(*) FROM fed_outbox GROUP BY peer_id")}
    fp, err = None, None
    try:
        fp = own_fingerprint() if enabled() else None
    except FedError as ex:
        err = str(ex)
    with _db() as c:
        dirn = {r[0]: r[1] for r in c.execute("SELECT peer_id, COUNT(*) FROM fed_directory GROUP BY peer_id")}
    for p in ps:
        p.update(shares=counts.get(p["id"], 0), inbound=inbound.get(p["id"], 0), queued=queued.get(p["id"], 0), directory=dirn.get(p["id"], 0),
                 common=sorted(caps_of(p)), features={k: feature(p, k) for k in caps2()})
    return {"enabled": enabled(), "base_url": SELF_URL, "host": SELF_HOST, "fingerprint": fp, "key_error": err, "peers": ps,
            "log": [{"ts": r[0], "host": r[1], "event": r[2], "detail": r[3]} for r in logs],
            "invites": [{"id": r[0], "created": r[1], "expires": r[2]} for r in invites],
            "https": SELF_URL.startswith("https://") or ALLOW_HTTP, "protocol": PROTOCOL, "capabilities": CAPS + caps2(),
            "scope": sharing.group_scope(), "groups": _all_groups()}


def _all_groups():
    with _db() as c:
        return sorted({r[0] for r in c.execute("SELECT name FROM groups UNION SELECT DISTINCT name FROM user_ext_group")})


OPTS_HOOKS = []              # fn(peer_id, alt, neu) - nach dem Speichern der Admin-Schalter (saga.fed_dir)


def set_opts(pid, d, by=""):
    """Admin-Schalter einer verbundenen Instanz setzen (nur bekannte Schluessel). -> neue Schalter oder None."""
    if not isinstance(d, dict):
        return None
    with _db() as c, c:
        p = peer_by(c, "id", pid)
        if not p:
            return None
        old = p["opts"]
        new = peer_opts(dict(old, **{k: v for k, v in d.items() if k in OPT_DEFAULTS}))
        if new == old:
            return new
        c.execute("UPDATE fed_peer SET opts=? WHERE id=?", (json.dumps(new), pid))
        log(c, "peer.options", p, (by + ": " if by else "") + " ".join(f"{k}={'on' if v else 'off'}" if isinstance(v, bool) else f"{k}={','.join(v) or '-'}"
                                                                   for k, v in new.items() if v != old.get(k)))
    for fn in list(OPTS_HOOKS):
        try:
            fn(pid, old, new)
        except Exception as ex:  # noqa: BLE001
            print(f"federation: opts hook: {type(ex).__name__}: {ex}", flush=True)
    return new


def make_invite(by):
    """Neuer Einladungscode (einmalig, 24 h). Gespeichert nur als Hash."""
    code = secrets.token_urlsafe(24)
    with _db() as c, c:
        c.execute("INSERT INTO fed_invite (code_hash, created, expires, created_by) VALUES (?,?,?,?)",
                  (hashlib.sha256(code.encode()).hexdigest(), time.time(), time.time() + INVITE_TTL, by))
        log(c, "invite.created", None, by or "", host=SELF_HOST)
    return code


def drop_invite(iid):
    with _db() as c, c:
        return c.execute("DELETE FROM fed_invite WHERE id=? AND used_at IS NULL", (iid,)).rowcount


def preview(url):
    """Schritt 1 beim Verbinden: Angaben der anderen Instanz holen (Fingerprint zum Vergleichen). FedError."""
    base = norm_base(url)
    if not base:
        raise FedError(t("Adresse bitte als https://server angeben (ohne Pfad)."))
    if host_of(base) == SELF_HOST:
        raise FedError(t("Das ist dieser Server."))
    return fetch_info(base)


def connect(url, code, fingerprint_):
    """Schritt 2: Verbindungsanfrage (signiert, mit Code). fingerprint_: der vom Admin bestaetigte Fingerprint."""
    info = preview(url)
    if info["fingerprint"] != fingerprint_:
        raise FedError(t("Der Schlüssel der anderen Instanz hat sich seit der Anzeige geändert – bitte neu prüfen."))
    priv, pub = own_key()
    peer_tmp = {"base_url": info["base_url"], "public_key": info["public"]}
    body = {"base_url": SELF_URL, "name": title(), "key": fedsig.b64u(pub), "code": str(code or "").strip()}
    status, d = call_json(peer_tmp, "POST", "/fed/v1/connect", body=body)
    if status != 200:
        msg = {403: t("Einladungscode ungültig oder abgelaufen (oder Schlüssel passt nicht)."),
               409: t("Die Instanzen sind schon verbunden.")}.get(status) or f"HTTP {status}: {(d or {}).get('error', '')}"
        raise FedError(msg, status)
    now = time.time()
    with _db() as c, c:
        old = peer_by(c, "host", info["host"])
        if old and old["status"] == "active":
            raise FedError(t("Die Instanzen sind schon verbunden."))
        if old:
            c.execute("UPDATE fed_peer SET base_url=?, name=?, public_key=?, fingerprint=?, status='pending_out', created=?, confirmed_at=NULL, "
                      "last_error=NULL WHERE id=?", (info["base_url"], info["name"], info["public"], info["fingerprint"], now, old["id"]))
            pid = old["id"]
        else:
            pid = _free_id(c)
            if pid is None:
                raise FedError(t("Zu viele verbundene Instanzen."))
            c.execute("INSERT INTO fed_peer (id, host, base_url, name, public_key, fingerprint, status, created) VALUES (?,?,?,?,?,?,?,?)",
                      (pid, info["host"], info["base_url"], info["name"], info["public"], info["fingerprint"], "pending_out", now))
        log(c, "connect.sent", peer_by(c, "id", pid), info["fingerprint"])
    return pid


def confirm(pid, by=""):
    """Admin bestaetigt eine eingegangene Anfrage -> aktiv; Gegenseite wird informiert. -> (ok, fehler der Zustellung)"""
    with _db() as c, c:
        p = peer_by(c, "id", pid)
        if not p or p["status"] != "pending_in":
            return False, None
        c.execute("UPDATE fed_peer SET status='active', confirmed_at=? WHERE id=?", (time.time(), pid))
        log(c, "peer.confirmed", p, by)
    p["status"] = "active"
    try:
        status, _ = call_json(p, "POST", "/fed/v1/confirm", body={})
        err = None if status == 200 else f"HTTP {status}"
    except FedError as ex:
        err = str(ex)
    with _db() as c, c:
        _touch(c, p, err)
    if err is None:
        hello(pid)                           # Protokoll 2: Faehigkeiten austauschen (aeltere Gegenstelle: Protokoll 1)
    return True, err


def refresh(pid):
    """Status bei der Gegenseite nachfragen (wartet sie noch / sind wir dort bestaetigt?)."""
    with _db() as c:
        p = peer_by(c, "id", pid)
    if not p or p["status"] not in ("pending_out", "active"):
        return None
    try:
        status, d = call_json(p, "GET", "/fed/v1/status")
    except FedError as ex:
        with _db() as c, c:
            _touch(c, p, ex)
        return f"error: {ex}"
    st = (d or {}).get("status") if status == 200 else None
    with _db() as c, c:
        _touch(c, p)
        if st == "active" and p["status"] == "pending_out":
            c.execute("UPDATE fed_peer SET status='active', confirmed_at=? WHERE id=?", (time.time(), pid))
            log(c, "peer.confirmed_remote", p, "status")
    if st == "active":
        hello(pid)
    return st or f"HTTP {status}"


def disconnect(pid, by=""):
    """Trennen (oder Anfrage ablehnen): sofort wirksam, Freigaben weg, Gegenseite best effort informieren."""
    with _db() as c, c:
        p = peer_by(c, "id", pid)
        if not p or p["status"] == "disconnected":
            return False
        c.execute("UPDATE fed_peer SET status='disconnected' WHERE id=?", (pid,))
        n = cleanup_peer(c, p)
        log(c, "peer.disconnected", p, f"{by} shares={n}")
    try:
        call_json(p, "POST", "/fed/v1/disconnect", body={})
    except FedError:
        pass
    return True


def remove(pid):
    """Getrennte Gegenstelle ganz aus der Liste entfernen (Protokoll bleibt)."""
    with _db() as c, c:
        p = peer_by(c, "id", pid)
        if not p or p["status"] != "disconnected":
            return False
        cleanup_peer(c, p)
        c.execute("DELETE FROM fed_peer WHERE id=?", (pid,))
        log(c, "peer.removed", None, "", host=p["host"])
    return True
