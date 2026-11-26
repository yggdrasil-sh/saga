"""Ort "Saga" (Saga 2.6, Bugs-DB 2269 K-I, Konzept K8/K10/K11/K13/K18/K19, M1/M3/M4/M7/M11): Einwilligung,
Anbieter-/Host-Hinweis, Gespraech mit der KI im eigenen Bereich und "Meine Daten".

Einwilligung (K19, Apple 5.1.2(i), Art. 7 DSGVO) - je Person in ai_consent (Protokoll, nie ueberschrieben):
  grant    Kennenlernen-Karte "Ja, mit Ideen" / "Ja, ohne Ideen": Zeitpunkt, Fassung des Textes (CONSENT_V), Host und Art
           des Endpunkts, die die Karte nannte (Nachweis, was die Person gelesen hat)
  later    "Jetzt nicht": keine Einwilligung; "Heute" zeigt die Karte nicht mehr, jeder Einstieg in den Ort "Saga" schon
  withdraw Einstellungen > Saga > Meine Daten: Widerruf -> jobs.pause_all(uid, "einwilligung") (RT-36), Ideen aus
  Zustand (consent): none | later | granted | withdrawn | stale. stale = erteilt, aber fuer einen ANDEREN Host: hat der
  Admin den Endpunkt gewechselt, gilt die alte Einwilligung nicht mehr (die Daten gingen sonst an jemand anderen) - die
  Karte erscheint wieder, laufende Auftraege halten beim naechsten Schritt an (jobs._precheck -> consent_ok).
  consent_ok(uid) = granted und Host wie jetzt. Wirkt auf: Auftraege (jobs.consent_ok, vor jedem Schritt), Ideen
  (ideas_ok), das Gespraech im Ort "Saga" (POST /api/ai/thread). "Frag deine Seiten", die Seiten-Hilfen und die KI im
  Chat bleiben wie bisher (Faehigkeit "ai", Hinweis bei jeder Nutzung) - siehe docs/APP-API-KI.md Abschnitt 9.

Anbieter-/Host-Hinweis (M4, RT-39) bildet NUR der Server (endpoint_hint), aus der Adresse des Modell-Endpunkts:
  host = ai.host_of (Host[:Port]), net = "home" nur, wenn alle festgehaltenen IPs (bzw. die IP in der Adresse) privat
  sind (_private: RFC 1918, Loopback, Link-Local, ULA - nicht CGNAT/6to4), sonst "internet" (auch ohne festgehaltene IPs). Bekannte Anbieter (KNOWN, nur bei
  "internet") mit Name und Land. Der Admin kann EINEN Zusatzsatz anfuegen (setting ai.endpoint_note): eine Zeile,
  hoechstens NOTE_MAX Zeichen, nur der erste Satz; bei net "internet" faellt er weg, sobald er "lokal"/"im Heimnetz"
  o. Ae. behauptet (_claims_local, ueber eine gefaltete Form ohne Leer-/Sonderzeichen in de/en/es/ru/th).

Gespraech (K10): ai_thread (je Person eines) + ai_turn (role me|ai). Kein E2E-Chat und nicht in der Chat-Liste.
  Verlauf an (ai.history_on): Zuege bleiben (hoechstens TURNS_MAX); Verlauf aus: expires = +24 h (Rueckbezuege),
  danach loescht cleanup() sie. Das Modell bekommt die letzten CONTEXT_TURNS Zuege der letzten 24 h als Gespraech.
  Kontext wie "Frag deine Seiten" ueber saga.ai_ctx (way "ask"), Kontingent/Spur wie dort (ai._gate, ai._respond).
  Einhaengepunkt fuer Plan-Karte/Rueckmeldung (Rolle kb): ai_turn.zug/fields/job_id - siehe docs/APP-API-KI.md 9.4.

Meine Daten (M7, Art. 15/17/20): GET /api/ai/mydata?format=json|md (Export: Einwilligungen, Einstellungen, Auftraege
mit Schritten/Protokoll/Aenderungen, Gespraech, Verlauf, eigene Zaehler), POST /api/ai/mydata/delete {confirm: true}
(loescht Auftraege samt Protokoll, Gespraech, Verlauf; Einwilligungs-Protokoll und Zaehler bleiben - siehe dort).
Aufbewahrung (M11): Gespraech ohne Verlauf 24 h, mit Verlauf wie ai_history (hoechstens 200); Auftraege: jobs.cleanup
(Klartext 30, Protokoll 90 Tage). Kontoloeschung: USER_TABLES ai_consent, ai_thread, ai_turn.

Endpunkte (Sitzung oder Geraete-Anmeldung, X-Saga: 1 - wie alle /api/ai/*):
  GET    /saga                         Ort "Saga" (Web)
  POST   /api/ai/consent {answer: ideas|no_ideas|later, version}   -> {consent}
  DELETE /api/ai/consent                                            -> {consent, paused}
  GET/PUT /api/ai/prefs (Endpunkt in saga/ai_say.py, K-H) + Felder ideas, sources, history, notify_content, dictation
  GET    /api/ai/thread · POST /api/ai/thread {q, tasks?, calendar?} (SSE) · DELETE /api/ai/thread
  GET    /api/ai/mydata?format=json|md · POST /api/ai/mydata/delete {confirm: true}
Tests: tests/kollegin_einwilligung.py, tests/browser_kollegin_kennenlernen.js
"""
import asyncio
import ipaddress
import json
import re
import time
import unicodedata
from datetime import datetime
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from db import connect as _db
from saga import ai_ctx, caps
from saga.core import TZ, _api_guard, _bad, _body, t
from saga.netguard import is_public

router = APIRouter()

CONSENT_V = "k19-1"              # Fassung des Kennenlernen-Textes (neue Fassung = neue Zahl; alte Einwilligungen gelten weiter)
ACTIONS = ("grant", "later", "withdraw")
IDEAS = ("off", "today")         # "Auch beim Schreiben" kommt mit 2.7
PREFS_KV = "ai_prefs"
NOTE_MAX = 200                   # Zeichen fuer den Zusatzsatz des Admins
KEEP_OFF = 86400                 # s: Gespraech ohne Verlauf (Rueckbezuege)
TURNS_MAX = 200                  # Zuege je Person mit Verlauf (wie ai.HISTORY_MAX)
CONTEXT_TURNS = 6                # so viele fruehere Zuege gehen als Gespraech an das Modell
TURN_TEXT_MAX = 1500             # Zeichen je frueherem Zug im Modell-Kontext
EXPORT_JOBS = 500                # Auftraege je Export (neueste zuerst)

# Bekannte Anbieter (Host-Endung -> Name, Land als ISO-Code; None = je nach Region). Nur bei oeffentlicher Adresse.
KNOWN = (
    ("api.openai.com", "OpenAI", "US"), ("openai.azure.com", "Microsoft Azure", None),
    ("services.ai.azure.com", "Microsoft Azure", None), ("api.anthropic.com", "Anthropic", "US"),
    ("api.mistral.ai", "Mistral AI", "FR"), ("generativelanguage.googleapis.com", "Google", "US"),
    ("aiplatform.googleapis.com", "Google Cloud", None), ("api.groq.com", "Groq", "US"),
    ("openrouter.ai", "OpenRouter", "US"), ("api.together.xyz", "Together AI", "US"),
    ("api.deepseek.com", "DeepSeek", "CN"), ("api.x.ai", "xAI", "US"), ("api.perplexity.ai", "Perplexity", "US"),
    ("api.cohere.com", "Cohere", "CA"), ("api.cohere.ai", "Cohere", "CA"), ("api.fireworks.ai", "Fireworks AI", "US"),
    ("huggingface.co", "Hugging Face", "US"), ("api.deepinfra.com", "DeepInfra", "US"),
    ("inference.ionos.com", "IONOS", "DE"), ("api.scaleway.ai", "Scaleway", "FR"),
    ("api.aleph-alpha.com", "Aleph Alpha", "DE"), ("api.moonshot.cn", "Moonshot AI", "CN"),
    ("dashscope.aliyuncs.com", "Alibaba Cloud", "CN"), ("dashscope-intl.aliyuncs.com", "Alibaba Cloud", "SG"),
    ("ollama.com", "Ollama", "US"), ("api.cerebras.ai", "Cerebras", "US"), ("api.sambanova.ai", "SambaNova", "US"),
)


def _country(code):
    names = {"US": t("USA"), "FR": t("Frankreich"), "DE": t("Deutschland"), "CN": t("China"), "CA": t("Kanada"),
             "SG": t("Singapur")}
    return names.get(code)


# --- Anbieter-/Host-Hinweis (M4, RT-39) ---------------------------------------------------------------------------
# "lokal"-Behauptungen in de/en/es/ru/th - verglichen mit einer gefalteten Form (klein, ohne Akzente, nur Buchstaben und
# Ziffern), damit "L o k a l", "lo​kal" oder "LOKÁL" nicht durchrutschen. Lieber ein Satz zu viel weg als einer zu viel.
_LOCAL = ("lokal", "local", "heimnetz", "hausnetz", "zuhause", "daheim", "intern", "onprem", "selbstgehostet", "selfhosted",
          "eigenerserver", "eigenenserver", "eigenemserver", "eigenesnetz", "eigenennetz", "eigenemnetz", "diesemserver",
          "diesenserver", "dieserserver", "dieserechner", "diesemrechner", "verlasstnie", "verlaesstnie", "nichtinsinternet",
          "keininternet", "ohneinternet", "offline", "homenetwork", "athome", "ownserver", "ownnetwork", "ownmachine",
          "thisserver", "thismachine", "neverleave", "notontheinternet", "notsenttotheinternet", "privatenetwork",
          "redlocal", "encasa", "servidorpropio", "estosservidor", "esteservidor", "nosaledelared", "nuncasale",
          "локальн", "домашн", "внутренн", "этомсервер", "вашемсервер", "нашемсервер", "неуходит",
          "ในเครื่อง", "ภายใน", "บ้าน", "โลคัล",
          # Pruefer pki: weitere Sprachen und Umschreibungen (nl, tr, ja, zh; "im Haus", Intranet, Homeserver ...)
          "lokaal", "yerel", "ローカル", "本地", "内网", "intranet", "inhouse", "imhaus", "dashaus", "nachdraussen",
          "heimserver", "homeserver", "homelab", "unserserver", "unserenserver", "unseremserver", "unsererserver",
          "privatesnetz", "privatennetz", "privatemnetz", "imlan")
# Verschleierung: kyrillische/griechische Doppelgaenger -> lateinisch, Leetspeak-Ziffern -> Buchstaben (1 als l und als i)
_LOOK = str.maketrans("аеорсухкіјӏԁмтвнοαεκιντρχυ0345@$|7", "aeopcyxkijldmtbhoaekivtpxuoeasasl" + "t")


def _fold(s, one="l"):
    s = unicodedata.normalize("NFKD", str(s or "")).casefold()
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).translate(_LOOK).replace("1", one)
    return "".join(ch for ch in s if ch.isalnum())


def _claims_local(s):
    f = {_fold(s), _fold(s, "i")}
    return any(_fold(w) in x for w in _LOCAL for x in f)


def clean_note(s):
    """Zusatzsatz des Admins -> eine Zeile, erster Satz, hoechstens NOTE_MAX Zeichen (ohne Steuerzeichen/Markierungen)."""
    from saga import ai_frame
    s = re.sub(r"[\x00-\x1f\x7f​-‏ -‮⁠-⁤﻿]+", " ", str(s or ""))
    s = ai_frame.neutral(s, "")
    s = re.sub(r"\s{2,}", " ", s).strip()
    m = re.search(r"[.!?。](\s|$)", s)
    if m:
        s = s[:m.end()].strip()
    return s[:NOTE_MAX].strip()


def _ip_lit(host):
    try:
        return str(ipaddress.ip_address(host.strip("[]")))
    except ValueError:
        return None


# "im Heimnetz" nur bei rein privaten Adressen (RFC 1918, Loopback, Link-Local, ULA). netguard.is_public ist fuer SSRF
# absichtlich weit ("nicht oeffentlich" = auch CGNAT 100.64/10, 6to4, Doku-Netze, Unlesbares) - das hiesse hier "Heimnetz".
_HOME_NETS = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8",
                                                      "169.254.0.0/16", "::1/128", "fc00::/7", "fe80::/10"))


def _private(ip):
    try:
        a = ipaddress.ip_address(str(ip).split("%")[0].strip("[]"))
    except ValueError:
        return False
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    return any(a.version == n.version and a in n for n in _HOME_NETS)


def provider_of(hostname):
    h = (hostname or "").lower().rstrip(".")
    for dom, name, cc in KNOWN:
        if h == dom or h.endswith("." + dom):
            return name, cc
    return None, None


def endpoint_hint(c=None):
    """Anbieter-/Host-Hinweis fuer Karte und Leiste -> {host, net, provider?, country?, country_name?, note?, label}
    oder None (kein Endpunkt). label = fertiger Text in der Sprache der Anfrage."""
    from saga import ai
    c = c if c is not None else ai.cfg()
    pu = urlparse(c.get("url") or "")
    name = (pu.hostname or "").lower()
    if not name:
        return None
    host = ai.host_of(c)
    lit = _ip_lit(name)
    ips = [lit] if lit else [str(x) for x in c.get("pinned") or [] if x]
    home = bool(ips) and all(_private(ip) and not is_public(ip) for ip in ips)
    out = {"host": host, "net": "home" if home else "internet"}
    if not home:
        prov, cc = provider_of(name)
        if prov:
            out["provider"] = prov
            if cc:
                out["country"] = cc
                out["country_name"] = _country(cc) or cc
    note = clean_note(c.get("endpoint_note"))
    if note and not (out["net"] == "internet" and _claims_local(note)):     # RT-39: oeffentliche IP -> nie "lokal"
        out["note"] = note
    out["label"] = label(out)
    return out


def label(h):
    if not h:
        return ""
    if h["net"] == "home":
        return t("{host} (im Heimnetz)", host=h["host"])
    if h.get("provider") and h.get("country_name"):
        return t("{host} ({provider}, {country})", host=h["host"], provider=h["provider"], country=h["country_name"])
    if h.get("provider"):
        return t("{host} ({provider}, im Internet)", host=h["host"], provider=h["provider"])
    return t("{host} (im Internet)", host=h["host"])


# --- Einwilligung -------------------------------------------------------------------------------------------------
def _hostname(c=None):
    from saga import ai
    c = c if c is not None else ai.cfg()
    return (urlparse(c.get("url") or "").hostname or "").lower()


def _rows(conn, uid):
    return conn.execute("SELECT id, at, action, version, ideas, host, net, provider FROM ai_consent WHERE uid=? ORDER BY id DESC",
                        (uid,)).fetchall()


def consent(uid, conn=None, cfg=None):
    """Zustand -> {state, at?, version?, ideas?, host?}. state: none | later | granted | withdrawn | stale."""
    if conn is None:
        with _db() as c:
            return consent(uid, c, cfg)
    r = conn.execute("SELECT at, action, version, ideas, host FROM ai_consent WHERE uid=? AND action IN ('grant', 'withdraw') "
                     "ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
    if r is None:
        later = conn.execute("SELECT at FROM ai_consent WHERE uid=? AND action='later' ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
        return {"state": "later", "at": later[0]} if later else {"state": "none"}
    if r[1] == "withdraw":
        return {"state": "withdrawn", "at": r[0]}
    out = {"state": "granted", "at": r[0], "version": r[2], "ideas": r[3] if r[3] in IDEAS else "off", "host": r[4]}
    if r[4] != _hostname(cfg):
        out["state"] = "stale"
    return out


def consent_ok(uid, conn=None, cfg=None):
    """Einwilligung erteilt und fuer den jetzigen Endpunkt (jobs._precheck, Ideen, Gespraech)."""
    if not uid:
        return False
    return consent(uid, conn, cfg)["state"] == "granted"


def ideas_ok(uid, conn=None):
    """Ideen von sich aus (ab K-C): Einwilligung + Faehigkeit ai_ideas + Ideen-Stufe der Person nicht aus."""
    if conn is None:
        with _db() as c:
            return ideas_ok(uid, c)
    return consent_ok(uid, conn) and caps.can(uid, "ai_ideas", conn) and prefs(uid, conn)["ideas"] != "off"


def grant(uid, ideas="off", conn=None, version=CONSENT_V):
    """Einwilligung erteilen (Kennenlernen-Karte) -> neuer Zustand. Ideen nur mit Faehigkeit ai_ideas."""
    if conn is None:
        with _db() as c, c:
            return grant(uid, ideas, c, version)
    h = endpoint_hint() or {"host": "", "net": ""}
    ideas = ideas if ideas in IDEAS and caps.can(uid, "ai_ideas", conn) else "off"
    conn.execute("INSERT INTO ai_consent (uid, at, action, version, ideas, host, net, provider) VALUES (?,?,?,?,?,?,?,?)",
                 (uid, time.time(), "grant", version, ideas, _hostname(), h.get("net") or "", h.get("provider")))
    _set_prefs(conn, uid, {"ideas": ideas})
    return consent(uid, conn)


def later(uid, conn=None):
    if conn is None:
        with _db() as c, c:
            return later(uid, c)
    conn.execute("INSERT INTO ai_consent (uid, at, action, version) VALUES (?,?,?,?)", (uid, time.time(), "later", CONSENT_V))
    return consent(uid, conn)


def withdraw(uid):
    """Widerruf (M7, RT-36): Protokoll, Ideen aus, laufende Auftraege anhalten -> (zustand, angehaltene)."""
    with _db() as c, c:
        c.execute("INSERT INTO ai_consent (uid, at, action, version) VALUES (?,?,?,?)", (uid, time.time(), "withdraw", CONSENT_V))
        _set_prefs(c, uid, {"ideas": "off"})
    from saga import jobs
    n = jobs.pause_all(uid, "einwilligung")
    return consent(uid), n


def intro(uid, conn=None, cfg=None):
    """Text der Kennenlernen-Karte (K19) - vom Server gebildet, damit Web und Apps genau dasselbe zeigen."""
    from saga import ai
    c = cfg if cfg is not None else ai.cfg()
    h = endpoint_hint(c)
    name = ai.persona_name()
    where = h["label"] if h else ""
    ideas_cap = caps.can(uid, "ai_ideas", conn, c)
    lines = [t("Ich bin {name}, die KI eures Servers.", name=name),
             t("Ich sehe nur, was du sehen darfst. Deine Frage und passende Ausschnitte aus deinen Seiten, Aufgaben und Terminen gehen an {where}.", where=where),  # noqa: E501
             t("Ich ändere nichts und schreibe niemandem ohne dein OK.")]
    try:                                         # K-H (2268): derselbe Text als Baustein "intro" - mit Anrede du/Sie der Person
        from saga import ai_say, i18n
        lang = i18n.get_lang()
        txt = ai_say.say("intro", {"name": name, "host": where}, lang, ai_say.persona_for(uid, conn))
        parts = ai_say.sentences(txt, lang)
        if len(parts) == 3 and where in parts[1]:
            lines = parts
    except Exception:  # noqa: BLE001 - Baustein fehlt/kaputt: die Saetze oben (Systemtext) bleiben
        pass
    details = [t("Nicht Ende-zu-Ende verschlüsselt: Was du {name} schreibst, liest die KI auf {host} im Klartext.", name=name,
                 host=h["host"] if h else ""),
               t("Was genau rausgeht: deine Frage, die letzten Sätze dieses Gesprächs und Ausschnitte aus Seiten, Aufgaben und Terminen, die zur Frage passen – nur, was du selbst sehen darfst, und nur aus Quellen, die du eingeschaltet hast. Chats und Seiten von anderen Servern liest sie nicht."),  # noqa: E501
               (t("Die Adresse liegt in eurem Heimnetz.") if h and h["net"] == "home" else
                t("Der Dienst steht im Internet; sein Betreiber ist {provider}.", provider=h["provider"]) if h and h.get("provider") else
                t("Der Dienst steht im Internet."))]
    details.append(t("Du kannst die Einwilligung jederzeit widerrufen: Einstellungen › Saga › Meine Daten."))
    out = {"version": CONSENT_V, "lines": lines, "details": details, "ideas_allowed": ideas_cap,
           "question": t("Soll ich dir ab und zu Ideen zeigen – nur auf „Heute“, nie als Mitteilung?") if ideas_cap else None,
           "bar": t("Was du hier schreibst, liest die KI auf {where}", where=where)}
    if h and h.get("note"):
        out["note"] = h["note"]
    return out


def status_fields(uid, conn, cfg):
    """Zusatzfelder fuer GET /api/ai/status (nur angehaengt, aeltere Felder unveraendert)."""
    return {"endpoint": endpoint_hint(cfg), "consent": consent(uid, conn, cfg), "intro": intro(uid, conn, cfg)}


# --- Einstellungen der Person (/api/ai/prefs) ---------------------------------------------------------------------
# Eigene Schluessel im gemeinsamen Eintrag user_kv "ai_prefs" (K-H 2268 haelt dort salutation/lively - fremde Schluessel
# bleiben immer stehen). GET/PUT /api/ai/prefs bedient saga/ai_say.py; die Felder hier haengt prefs_extra()/prefs_put() an.
PREF_VALUES = {"ideas": IDEAS, "notify_content": ("unlocked", "never"), "dictation": ("server", "device")}
PREF_DEFAULT = {"ideas": "off", "notify_content": "unlocked", "dictation": "server"}


def _raw_prefs(conn, uid):
    r = conn.execute("SELECT v FROM user_kv WHERE uid=? AND k=?", (uid, PREFS_KV)).fetchone()
    try:
        d = json.loads(r[0]) if r and r[0] else {}
    except ValueError:
        d = {}
    return d if isinstance(d, dict) else {}


def _load_prefs(conn, uid):
    d = _raw_prefs(conn, uid)
    out = dict(PREF_DEFAULT)
    for k, vals in PREF_VALUES.items():
        if d.get(k) in vals:
            out[k] = d[k]
    return out


def _set_prefs(conn, uid, d):
    raw = _raw_prefs(conn, uid)
    for k, v in d.items():
        if k in PREF_VALUES and v in PREF_VALUES[k]:
            if v == PREF_DEFAULT[k]:
                raw.pop(k, None)
            else:
                raw[k] = v
    if raw:
        conn.execute("INSERT OR REPLACE INTO user_kv (uid, k, v) VALUES (?, ?, ?)", (uid, PREFS_KV, json.dumps(raw, sort_keys=True)))
    else:
        conn.execute("DELETE FROM user_kv WHERE uid=? AND k=?", (uid, PREFS_KV))
    return _load_prefs(conn, uid)


def prefs(uid, conn=None):
    if conn is None:
        with _db() as c:
            return prefs(uid, c)
    out = _load_prefs(conn, uid)
    if not consent_ok(uid, conn):                            # ohne Einwilligung keine Ideen (RT-36), egal was gespeichert ist
        out["ideas"] = "off"
    return out


def prefs_extra(uid, conn):
    """Felder von K-I fuer GET/PUT /api/ai/prefs (an die Antwort von saga/ai_say.py angehaengt)."""
    from saga import ai
    c = ai.cfg()
    return dict(prefs(uid, conn), sources=ai_ctx.prefs(uid, conn), history=ai.history_on(conn, uid),
                history_allowed=bool(c.get("history", True)), ideas_allowed=caps.can(uid, "ai_ideas", conn, c),
                consent=consent(uid, conn, c))


_prefs_view = prefs_extra


def prefs_check(b):
    """PUT /api/ai/prefs: Form der K-I-Felder pruefen (vor jedem Schreiben) -> Fehlerantwort oder None."""
    for k, vals in PREF_VALUES.items():
        if k in b and b[k] not in vals:
            return _bad(t("ungültige Angabe"))
    if "sources" in b and not ai_ctx.valid(b["sources"]):
        return _bad(t("ungültige Angabe"))
    if "history" in b and not isinstance(b["history"], bool):
        return _bad(t("ungültige Angabe"))
    return None


def prefs_put(uid, b, conn):
    """PUT /api/ai/prefs: K-I-Felder in derselben Transaktion setzen -> Fehlerantwort oder None."""
    from saga import ai
    c = ai.cfg()
    if b.get("ideas") == "today":
        if not consent_ok(uid, conn, c):
            return _bad(t("Ideen gehen erst, wenn du {name} kennengelernt hast.", name=ai.persona_name()), 409)
        if not caps.can(uid, "ai_ideas", conn, c):
            return _bad(t("Ideen sind für dich nicht freigeschaltet."), 403)
    if b.get("history") and not c.get("history", True):
        return _bad(t("Der Verlauf ist auf diesem Server abgeschaltet."), 409)
    _set_prefs(conn, uid, {k: b[k] for k in PREF_VALUES if k in b})
    if "sources" in b:
        ai_ctx.set_prefs(uid, b["sources"], conn)
    if "history" in b:
        _set_history(conn, uid, b["history"])
    return None


def _set_history(conn, uid, on):
    """Verlauf an/aus (wie PUT /api/ai/settings): aus = gespeicherter Verlauf weg, Gespraech nur noch 24 h."""
    now = time.time()
    if on:
        conn.execute("INSERT OR REPLACE INTO user_kv (uid, k, v) VALUES (?, 'ai_history', '1')", (uid,))
        return
    conn.execute("DELETE FROM user_kv WHERE uid=? AND k='ai_history'", (uid,))
    conn.execute("DELETE FROM ai_history WHERE uid=?", (uid,))
    conn.execute("DELETE FROM ai_turn WHERE uid=? AND created < ?", (uid, now - KEEP_OFF))
    conn.execute("UPDATE ai_turn SET expires = created + ? WHERE uid=? AND expires IS NULL", (KEEP_OFF, uid))


# --- Gespraech (ai_thread / ai_turn) ------------------------------------------------------------------------------
def _thread(conn, uid, create=False):
    r = conn.execute("SELECT id FROM ai_thread WHERE uid=?", (uid,)).fetchone()
    if r or not create:
        return r[0] if r else None
    now = time.time()
    conn.execute("INSERT INTO ai_thread (uid, created, updated) VALUES (?, ?, ?) ON CONFLICT (uid) DO NOTHING", (uid, now, now))
    return conn.execute("SELECT id FROM ai_thread WHERE uid=?", (uid,)).fetchone()[0]


def add_turn(uid, role, text, *, zug=None, fields=None, read=None, job_id=None):
    """Zug anlegen -> n. Ohne Verlauf mit Ablauf in 24 h; mit Verlauf hoechstens TURNS_MAX Zuege."""
    from saga import ai
    now = time.time()
    with _db() as c, c:
        tid = _thread(c, uid, create=True)
        c.execute("SELECT id FROM ai_thread WHERE id=? FOR UPDATE", (tid,))
        n = (c.execute("SELECT MAX(n) FROM ai_turn WHERE thread_id=?", (tid,)).fetchone()[0] or 0) + 1
        keep = ai.history_on(c, uid)
        c.execute("INSERT INTO ai_turn (thread_id, uid, n, role, zug, text, fields, read, job_id, created, expires) "
                  "VALUES (?,?,?,?,?,?,CAST(? AS JSONB),CAST(? AS JSONB),?,?,?)",
                  (tid, uid, n, role, zug, text or "", json.dumps(fields) if fields is not None else None,
                   json.dumps(list(read)) if read is not None else None, job_id, now, None if keep else now + KEEP_OFF))
        c.execute("UPDATE ai_thread SET updated=? WHERE id=?", (now, tid))
        if keep:
            old = [r[0] for r in c.execute("SELECT id FROM ai_turn WHERE thread_id=? ORDER BY n DESC", (tid,)).fetchall()[TURNS_MAX:]]
            if old:
                c.executemany("DELETE FROM ai_turn WHERE id=?", [(i,) for i in old])
    return n


def _j(v):
    if v is None or isinstance(v, (list, dict)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


def turns(uid, conn=None, since=None):
    if conn is None:
        with _db() as c:
            return turns(uid, c, since)
    now = time.time()
    rows = conn.execute("SELECT t.n, t.role, t.zug, t.text, t.fields, t.read, t.job_id, t.created, t.expires FROM ai_turn t "
                        "JOIN ai_thread h ON h.id=t.thread_id WHERE h.uid=? AND (t.expires IS NULL OR t.expires > ?) "
                        "AND t.created >= ? ORDER BY t.n", (uid, now, since or 0)).fetchall()
    return [{"n": r[0], "role": r[1], "zug": r[2], "text": r[3], "fields": _j(r[4]), "read": _j(r[5]) or [], "job_id": r[6],
             "created": r[7], "expires": r[8]} for r in rows]


def thread_messages(uid, base):
    """Fruehere Zuege (letzte 24 h, hoechstens CONTEXT_TURNS) vor die Frage setzen - base = ai.ask_messages(...)."""
    prev = [x for x in turns(uid, since=time.time() - KEEP_OFF) if x["role"] in ("me", "ai") and x["text"]][-CONTEXT_TURNS:]
    hist = [{"role": "user" if x["role"] == "me" else "assistant", "content": x["text"][:TURN_TEXT_MAX]} for x in prev]
    return base[:1] + hist + base[1:], len(hist)


def cleanup(now=None):
    """Stuendlich (aus jobs.cleanup): abgelaufene Zuege und leere Gespraeche weg -> Anzahl Zuege."""
    now = now if now is not None else time.time()
    with _db() as c, c:
        n = c.execute("DELETE FROM ai_turn WHERE expires IS NOT NULL AND expires < ?", (now,)).rowcount
        c.execute("DELETE FROM ai_thread h WHERE h.updated < ? AND NOT EXISTS (SELECT 1 FROM ai_turn t WHERE t.thread_id=h.id)",
                  (now - KEEP_OFF,))
    return n


# --- Meine Daten: Export und Loeschen (M7, RT-35) -----------------------------------------------------------------
def export(uid):
    """Alles, was die Kollegin ueber die Person gespeichert hat (JSON-tauglich)."""
    from saga import ai
    with _db() as c:
        cons = [{"at": r[1], "action": r[2], "version": r[3], "ideas": r[4], "host": r[5], "net": r[6], "provider": r[7]}
                for r in _rows(c, uid)]
        jobs_out = []
        for r in c.execute("SELECT id, kind, source, state, sub, title, plan, plan_v, created, updated, ended_at, reason, units, unit "
                           "FROM job WHERE uid=? ORDER BY id DESC LIMIT ?", (uid, EXPORT_JOBS)).fetchall():
            jid = r[0]
            steps = [{"n": s[0], "tool": s[1], "klass": s[2], "undo_class": s[3], "args": _j(s[4]), "state": s[5], "result": _j(s[6]),
                      "done_at": s[7]} for s in c.execute("SELECT n, tool, klass, undo_class, args, state, result, done_at FROM job_step "
                                                          "WHERE job_id=? ORDER BY n", (jid,)).fetchall()]
            events = [{"at": e[0], "kind": e[1], "data": _j(e[2])} for e in
                      c.execute("SELECT at, kind, data FROM job_event WHERE job_id=? ORDER BY id", (jid,)).fetchall()]
            changes = [{"step": x[0], "target": x[1], "at": x[2], "before": _j(x[3]), "after": _j(x[4]), "undone_at": x[5]} for x in
                       c.execute("SELECT step_n, target, at, before, after, undone_at FROM job_change WHERE job_id=? ORDER BY id",
                                 (jid,)).fetchall()]
            jobs_out.append({"id": jid, "kind": r[1], "source": r[2], "state": r[3], "sub": r[4], "title": r[5], "plan": _j(r[6]),
                             "plan_v": r[7], "created": r[8], "updated": r[9], "ended_at": r[10], "reason": r[11],
                             "units": r[12], "unit": r[13], "steps": steps, "events": events, "changes": changes})
        hist = [{"id": r[0], "created": r[1], "kind": r[2], "question": r[3], "answer": r[4], "sources": _j(r[5])} for r in
                c.execute("SELECT id, created, kind, question, answer, sources FROM ai_history WHERE uid=? ORDER BY id", (uid,)).fetchall()]
        usage = [{"day": r[0], "n": r[1]} for r in c.execute("SELECT day, n FROM ai_usage WHERE uid=? ORDER BY day", (uid,)).fetchall()]
        units = [{"day": r[0], "pool": r[1], "units": r[2], "real_in": r[3], "real_out": r[4]} for r in
                 c.execute("SELECT day, pool, units, real_in, real_out FROM ai_units WHERE uid=? ORDER BY day, pool", (uid,)).fetchall()]
        pv = _prefs_view(uid, c)
        conv = turns(uid, c)
    return {"format": "saga-ai-export/1", "exported_at": time.time(), "account": uid, "persona": ai.persona_name(),
            "endpoint": endpoint_hint(), "consent": cons, "prefs": pv, "jobs": jobs_out, "conversation": conv,
            "history": hist, "counters": {"requests": usage, "units": units}}


def _ts(v):
    return datetime.fromtimestamp(float(v), TZ).strftime("%Y-%m-%d %H:%M") if v else "–"


def export_md(d):
    """Markdown-Fassung des Exports (lesbar; die JSON-Fassung ist vollstaendig)."""
    out = [f"# {t('Meine Daten bei {name}', name=d['persona'])}", "", f"{t('Konto')}: {d['account']} · {t('Stand')}: {_ts(d['exported_at'])}", ""]
    out += [f"## {t('Einwilligungen')}", ""]
    acts = {"grant": t("erteilt"), "later": t("„Jetzt nicht“"), "withdraw": t("widerrufen")}
    out += [f"- {_ts(x['at'])} · {acts.get(x['action'], x['action'])} · {x['version'] or '–'} · {x['host'] or '–'}" for x in d["consent"]] or ["–"]
    out += ["", f"## {t('Gespräch')}", ""]
    for x in d["conversation"]:
        who = t("Ich") if x["role"] == "me" else d["persona"]
        out.append(f"**{who}** ({_ts(x['created'])}): {x['text']}")
        out.append("")
    if not d["conversation"]:
        out += ["–", ""]
    out += [f"## {t('Aufträge')}", ""]
    for j in d["jobs"]:
        out.append(f"### {j['title'] or t('Auftrag {n}', n=j['id'])} · {j['state']}")
        out.append(f"{t('Angelegt')}: {_ts(j['created'])} · {t('Ende')}: {_ts(j['ended_at'])}")
        out += [f"- {s['n']}. {s['tool']} ({s['klass']}, {s['state']})" for s in j["steps"]]
        out.append("")
    if not d["jobs"]:
        out += ["–", ""]
    out += [f"## {t('Verlauf')}", ""]
    for h in d["history"]:
        out += [f"**{t('Frage')}** ({_ts(h['created'])}): {h['question']}", "", h["answer"], ""]
    if not d["history"]:
        out += ["–", ""]
    out += [f"## {t('Zähler')}", ""]
    out += [f"- {u['day']}: {u['n']}" for u in d["counters"]["requests"]] or ["–"]
    out += [f"- {u['day']} {u['pool']}: {u['units']}" for u in d["counters"]["units"]]
    return "\n".join(out) + "\n"


def delete_all(uid):
    """Alles loeschen (M7): Auftraege samt Schritten/Protokoll/Aenderungen (CASCADE), Gespraech, Verlauf -> Anzahlen.
    Bleiben: das Einwilligungs-Protokoll (Nachweis fuer den Admin, Art. 7 Abs. 1) und die Tageszaehler (nur Zahlen, fuer
    das Kontingent; ai_usage 7, ai_units 90 Tage). Gesendete Nachrichten bleiben bei den Empfaengern."""
    with _db() as c, c:
        nj = c.execute("DELETE FROM job WHERE uid=?", (uid,)).rowcount
        nt = c.execute("DELETE FROM ai_turn WHERE uid=?", (uid,)).rowcount
        c.execute("DELETE FROM ai_thread WHERE uid=?", (uid,))
        nh = c.execute("DELETE FROM ai_history WHERE uid=?", (uid,)).rowcount
    return {"jobs": nj, "turns": nt, "history": nh}


# --- Seite und Endpunkte ------------------------------------------------------------------------------------------
def side_on(uid):
    """Eintrag "Saga" in der Seitenleiste (K10): nur mit Faehigkeit ai - sonst fehlt der Bereich ganz."""
    try:
        return bool(uid) and caps.can(uid, "ai")
    except Exception:  # noqa: BLE001 - Einstellung kaputt/DB kurz weg: Eintrag lieber weglassen als die Seite brechen
        return False


@router.get("/saga", response_class=HTMLResponse)
def saga_page(request: Request):
    from saga import avatars, chat, mfa
    from saga.core import ASSET_V, SEC_HEADERS, _session_user, remember_ui_lang, title, tmpl
    uid = _session_user(request)
    if not uid:
        return RedirectResponse("/login?next=/saga", status_code=303)
    if (go := mfa.setup_redirect(request, uid)) is not None:
        return go
    if not side_on(uid):
        return RedirectResponse("/dashboard", status_code=303)
    remember_ui_lang(uid, request)
    with _db() as c:
        n_chat = chat.unread_total(c, uid)
    html = tmpl.get_template("saga.html").render(title=title(), name=request.session.get("name", uid), uid=uid, v=ASSET_V,
                                                 chat_enabled=chat.enabled(), chat_unread=n_chat, me_avatar=avatars.my_url(uid),
                                                 side_cur="saga", saga_on=True)
    return HTMLResponse(html, headers=dict(SEC_HEADERS))


def _guard(request):
    return _api_guard(request)


def _ai_on(uid):
    from saga import ai
    return ai.allowed(uid)


def _noai():
    return _bad(t("Der KI-Assistent ist für dich nicht eingeschaltet."), 403)


@router.post("/api/ai/consent")
async def api_consent(request: Request):
    uid = _guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    ans = b.get("answer")
    if ans not in ("ideas", "no_ideas", "later"):
        return _bad(t("ungültige Angabe"))

    def run():
        if not _ai_on(uid):
            return _noai()
        if ans == "later":
            return JSONResponse({"consent": later(uid)})
        if b.get("version") != CONSENT_V:                  # Karte zeigte einen anderen Text: neu laden und neu fragen
            return JSONResponse({"error": t("Der Text hat sich geändert – bitte noch einmal lesen."), "version": CONSENT_V},
                                status_code=409)
        st = grant(uid, "today" if ans == "ideas" else "off")
        return JSONResponse({"consent": st, "prefs": prefs(uid)})
    return await asyncio.to_thread(run)


@router.delete("/api/ai/consent")
async def api_consent_withdraw(request: Request):
    uid = _guard(request)
    if not uid:
        return _bad("unauthorized", 401)

    def run():
        st, n = withdraw(uid)                              # auch ohne KI-Freigabe: Widerruf geht immer
        print(f"ai: Einwilligung widerrufen ({n} Auftraege angehalten)", flush=True)
        return JSONResponse({"consent": st, "paused": n})
    return await asyncio.to_thread(run)


@router.get("/api/ai/thread")
async def api_thread(request: Request):
    uid = _guard(request)
    if not uid:
        return _bad("unauthorized", 401)

    def run():
        from saga import ai
        if not _ai_on(uid):
            return _noai()
        with _db() as c:
            hist = ai.history_on(c, uid)
            out = {"turns": turns(uid, c), "history": hist, "keep": None if hist else KEEP_OFF,
                   "consent": consent(uid, c)}
        return JSONResponse(out, headers={"Cache-Control": "no-store"})
    return await asyncio.to_thread(run)


@router.delete("/api/ai/thread")
async def api_thread_clear(request: Request):
    uid = _guard(request)
    if not uid:
        return _bad("unauthorized", 401)

    def run():
        with _db() as c, c:
            n = c.execute("DELETE FROM ai_turn WHERE uid=?", (uid,)).rowcount
        return JSONResponse({"deleted": n})
    return await asyncio.to_thread(run)


@router.post("/api/ai/thread")
async def api_thread_ask(request: Request):
    """Gespraech im Ort "Saga": {q, tasks?, calendar?} -> text/event-stream wie /api/ai/ask (sources/status/delta/done),
    done zusaetzlich {turn: n} (Nummer des KI-Zugs). Ohne Einwilligung 403 {consent: "needed"} - nichts geht ans Modell."""
    from saga import ai
    uid = _guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    q = b.get("q") if isinstance(b.get("q"), str) else ""
    q = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", q).strip()
    if not q:
        return _bad(t("Bitte eine Frage eingeben."))
    if len(q) > ai.Q_MAX:
        return _bad(t("Die Frage ist zu lang (höchstens {n} Zeichen).", n=ai.Q_MAX))
    if not await asyncio.to_thread(_ai_on, uid):
        return _noai()
    if not await asyncio.to_thread(consent_ok, uid):        # K19: vor der Einwilligung geht nichts ans Modell
        return JSONResponse({"error": t("Lerne {name} zuerst kennen – vorher geht nichts an die KI.", name=ai.persona_name()),
                             "consent": "needed"}, status_code=403)
    c, err = await ai._gate(uid)
    if err is not None:
        return err
    try:
        with_tasks, with_cal = ai._bool(b.get("tasks")), ai._bool(b.get("calendar"))
        budget = max(1000, min(50000, int(c.get("context") or 8000)))
        cx = await asyncio.to_thread(ai_ctx.select, uid, q, way="ask", budget=budget,
                                     want={"pages": True, "tasks": with_tasks, "calendar": with_cal})
        quota = int(c.get("quota") or 0)
        msgs, nprev = await asyncio.to_thread(thread_messages, uid, ai.ask_messages(q, cx.text, cx.extra))   # vor dem neuen Zug
        await asyncio.to_thread(add_turn, uid, "me", q)
        if not cx.origins and not cx.extra and not nprev:  # nichts gefunden, kein Gespraech: ohne Modell (wie Frag deine Seiten)
            ai._release(uid)
            text = ai.no_match_text(q)
            n = await asyncio.to_thread(add_turn, uid, "ai", text, zug="no_match", read=cx.read)
            ql = await asyncio.to_thread(ai._quota_left, uid)
            return ai._static(text, {"quota_left": ql, "no_match": True, "read": list(cx.read), "turn": n}, [])
        left = await asyncio.to_thread(ai._take_quota, uid, quota)
        if left == -1:
            ai._release(uid)
            return _bad(t("Dein Tageskontingent für den Assistenten ist aufgebraucht."), 429)
        rid = ai.frame_rid(msgs)

        def finish(answer):
            out = {"read": cx.read}
            clean = ai.strip_markers(answer, rid).strip() if answer else ""
            if answer and clean != answer.strip():
                out["text"] = clean
            if clean:
                from saga import ai_frame
                out["turn"] = add_turn(uid, "ai", ai_frame.no_images(clean), zug="answer", read=cx.read,
                                       fields={"sources": [{"n": s["n"], "id": s["id"], "title": s.get("title")} for s in cx.origins]})
            return out
        return ai._respond(uid, c, msgs, [ai._fmt("sources", {"sources": cx.origins})], finish, left, quota)
    except BaseException:
        ai._release(uid)
        raise


@router.get("/api/ai/mydata")
async def api_mydata(request: Request):
    uid = _guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    fmt = request.query_params.get("format") or "json"
    if fmt not in ("json", "md"):
        return _bad(t("ungültige Angabe"))
    d = await asyncio.to_thread(export, uid)
    day = datetime.now(TZ).strftime("%Y-%m-%d")
    h = {"Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="saga-ki-{day}.{fmt}"'}
    if fmt == "md":
        return Response(export_md(d), media_type="text/markdown; charset=utf-8", headers=h)
    return Response(json.dumps(d, ensure_ascii=False, indent=1), media_type="application/json; charset=utf-8", headers=h)


@router.post("/api/ai/mydata/delete")
async def api_mydata_delete(request: Request):
    uid = _guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    if b.get("confirm") is not True:
        return _bad(t("Bitte bestätigen."))
    n = await asyncio.to_thread(delete_all, uid)
    print(f"ai: Meine Daten geloescht (Auftraege {n['jobs']}, Zuege {n['turns']}, Verlauf {n['history']})", flush=True)
    return JSONResponse({"ok": True, "deleted": n})

