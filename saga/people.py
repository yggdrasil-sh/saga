"""Personen in Seiten und Datenbanken (2.5, Paket 1 Etappe 2): wer ist eine "Person", wie wird sie gespeichert und gezeigt.

Grundlage fuer die Personen-Eigenschaft (Typ "person"), fuer @Erwaehnungen (Paket 6) und fuer task.assignee (Paket 7).

Formate
- Gespeichert (note.props, row_person, kuenftig task.assignee) wird die KANONISCHE Kennung: lokaler Benutzername bzw.
  Prinzipal '@name@host' (fremde Person; bei Personen mit Handle wie sharing.REMOTE_CANON). Sie verlaesst den Server nie roh.
- Nach aussen (Web, Apps) nur die Kennung ref (to_ref): lokale Konten IMMER als Handle '~…' (core.local_ref) - auch das
  eigene, markiert mit me=true -, fremde Personen als Prinzipal. Die Kennung haengt damit nicht vom Betrachter ab: in
  Ansichten gespeicherte Filter ("enthaelt <Person>") gelten fuer alle gleich. Dazu label_map() {ref: {name, avatar, me,
  remote, gone}} mit Anzeigename (sharing.names - nie der Anmeldename). An andere Instanzen schreibt saga.federation
  (fed_pages.dbx_out) lokale Konten auf '@oeffentlicher-name@host' um.
- Eingaben (from_ref) nehmen Handle, Prinzipal oder den eigenen Benutzernamen an - nie den Anmeldenamen eines anderen.
- Texte (CSV, Markdown, KI, Formeln): nur Anzeigenamen (text_values).

Wer ist eine Person (Konzept P1-E3): wer Zugriff auf die Seite/Datenbank hat - Eigentuemer, Freigaben an Personen,
Mitglieder freigegebener Gruppen, foederierte Personen mit Freigabe (auch geerbt von Seiten darueber). candidates()
liefert genau diese Liste (Auswahl "erwaehnbar/zuweisbar"); allowed() prueft eine einzelne Kennung.

API
  GET /api/notes/{nid}/people?q=   -> {people: [{ref, name, avatar, me, remote}], more} (hoechstens 20, 60/min je Konto;
                                       ab Rolle 'view'; Suche nach Anzeigename; ohne q: die ersten 20 nach Name)
Fuer andere Module: candidates(c, owner, nid, viewer, q), allowed(c, owner, nid, canon), to_ref(c, canon, viewer),
from_ref(c, viewer, x, owner, nid), label_map(c, canons, viewer), out_values(c, schema, rows, viewer),
person_label(c, canon) (Text fuer Export/KI)."""
import json
import re
import secrets
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from db import connect as _db
from saga import sharing
from saga.core import HANDLE_RE, _api_guard, _bad, local_ref, resolve_ref, t
from saga.notes import note_chain
from saga.sharing import Access, access, is_remote, names

router = APIRouter()

VALUES_MAX = 20                   # Personen je Zelle
CANDIDATES_MAX = 20               # Treffer der Auswahl
MEMBERS_MAX = 1000                # Mitglieder je freigegebener Gruppe
RATE = 60                         # Suchen je Minute und Konto
_PRINCIPAL = re.compile(r"@([a-z0-9][a-z0-9_-]{1,31}|~[a-z2-7]{16})@[a-z0-9.:-]{1,260}")
_hits = {}


def canon_remote(c, principal):
    """'@~handle@host' einer Person, die hier unter ihrem Namen gefuehrt wird -> '@name@host' (wie Freigaben)."""
    if sharing.REMOTE_CANON is not None and isinstance(principal, str) and principal.startswith("@"):
        try:
            return sharing.REMOTE_CANON(c, principal)
        except Exception:  # noqa: BLE001 - Foederation aus/kaputt: Kennung bleibt
            return principal
    return principal


# --- Wer hat Zugriff -------------------------------------------------------------------------------------------------
def _group_members(c, g):
    return [r[0] for r in c.execute(
        "SELECT u.username FROM users u WHERE u.disabled=0 AND (EXISTS (SELECT 1 FROM user_group ug JOIN groups gr ON gr.id=ug.group_id "
        "WHERE ug.user_id=u.id AND gr.name=?) OR EXISTS (SELECT 1 FROM user_ext_group e WHERE e.user_id=u.id AND e.name=?)) "
        "ORDER BY u.id LIMIT ?", (g, g, MEMBERS_MAX))]


def with_access(c, owner, nid):
    """Kanonische Kennungen aller Personen mit Zugriff auf nid (Seite oder Datenbank von owner): Eigentuemer, Freigaben auf
    nid und Seiten darueber an Personen, Gruppen (Mitglieder) und fremde Personen. Fremde Gruppen ('remote_group') kennt
    nur die andere Instanz - deren Mitglieder sind hier nicht aufzaehlbar (sie bleiben aber eingebbar, siehe allowed)."""
    out = [owner]
    if not sharing.enabled():
        return out
    chain = note_chain(c, owner, nid) or [nid]
    ph = ",".join("?" * len(chain))
    rows = c.execute(f"SELECT target_type, target FROM note_share WHERE owner_uid=? AND note_id IN ({ph}) ORDER BY created, id",
                     (owner, *chain)).fetchall()
    for typ, tg in rows:
        if typ == "user":
            out.append(tg)
        elif typ == "group":
            out.extend(_group_members(c, tg))
        elif typ == "remote" and sharing.fed_enabled():
            out.append(tg)
    active = {r[0] for r in c.execute(f"SELECT username FROM users WHERE disabled=0 AND username IN ({','.join('?' * len(out))})",
                                      tuple(out))} if out else set()
    return [u for u in dict.fromkeys(out) if is_remote(u) or u in active]


def allowed(c, owner, nid, canon):
    """Darf canon in nid zugewiesen/erwaehnt werden? Lokal: Zugriff auf die Seite (sharing.Access); fremd: Freigabe an
    genau diese Person auf nid oder einer Seite darueber (Mitglieder fremder Gruppen kennt nur deren Instanz - sie koennen
    sich selbst eintragen, siehe clean_values)."""
    if not canon:
        return False
    if canon == owner:
        return True
    if is_remote(canon):
        if not sharing.fed_enabled():
            return False
        chain = note_chain(c, owner, nid) or [nid]
        ph = ",".join("?" * len(chain))
        return bool(c.execute(f"SELECT 1 FROM note_share WHERE owner_uid=? AND note_id IN ({ph}) AND target_type='remote' AND target=? LIMIT 1",
                              (owner, *chain, canon)).fetchone())
    if not c.execute("SELECT 1 FROM users WHERE username=? AND disabled=0", (canon,)).fetchone():
        return False
    return bool(Access(c, canon).role(nid))


# --- Ausgabe ---------------------------------------------------------------------------------------------------------
def to_ref(c, canon, viewer=None):
    """Kennung nach aussen: lokale Konten als Handle (auch das eigene), fremde Personen als Prinzipal. Schon umgeschriebene
    Werte (Handle) und Grabsteine geloeschter Konten ('#gone-…') bleiben, wie sie sind (idempotent)."""
    if not isinstance(canon, str) or is_remote(canon) or HANDLE_RE.fullmatch(canon) or canon.startswith("#"):
        return canon
    return local_ref(canon)


def to_canon(c, x):
    """Kennung (Handle/Prinzipal/gespeicherter Wert) -> gespeicherte Form ('' = unbekanntes/gesperrtes Konto)."""
    if isinstance(x, str) and HANDLE_RE.fullmatch(x):
        return resolve_ref(c, x)
    return x if isinstance(x, str) else ""


def label_map(c, canons, viewer):
    """{ref: {name, avatar, me, remote, gone}} fuer die Kennungen canons, aus Sicht von viewer. Geloeschte/unbekannte
    Konten: name 'Geloeschte Person', gone=true (der Wert bleibt in der Zelle stehen, Konzept 2.4)."""
    canons = [x for x in dict.fromkeys(canons) if isinstance(x, str) and x]
    if not canons:
        return {}
    nm = names(c, [x for x in canons if not HANDLE_RE.fullmatch(x)])
    out = {}
    for u in canons:
        ref = to_ref(c, u, viewer)
        if u.startswith("#"):                          # Grabstein eines geloeschten Kontos
            out[ref] = {"name": t("Gelöschte Person"), "avatar": None, "me": False, "remote": False, "gone": True}
        elif u in nm:
            out[ref] = {"name": nm[u], "avatar": nm.avatar(u), "me": u == viewer, "remote": is_remote(u)}
        else:
            out[ref] = {"name": t("Gelöschte Person"), "avatar": None, "me": False, "remote": is_remote(u), "gone": True}
    return out


def person_label(c, canon, cache=None):
    """Anzeigename fuer Texte (Export, KI-Kontext): nie der Anmeldename. cache: names()-Ergebnis zum Wiederverwenden."""
    nm = cache if cache is not None else names(c, [canon])
    return nm.get(canon) or t("Gelöschte Person")


def person_pids(schema):
    return [p["id"] for p in (schema or {}).get("props") or [] if isinstance(p, dict) and p.get("type") == "person" and p.get("id")]


def out_values(c, schema, rows, viewer):
    """Werte der Personen-Eigenschaften in rows[i]['props'] (im Speicher) auf Kennungen des Betrachters umschreiben ->
    Woerterbuch people {ref: {...}} fuer die Antwort (leer, wenn es keine Personen gibt)."""
    pids = person_pids(schema)
    if not pids or not rows:
        return {}
    seen = []
    for r in rows:
        pv = r.get("props") if isinstance(r.get("props"), dict) else None
        if not pv:
            continue
        for pid in pids:
            v = pv.get(pid)
            if isinstance(v, list):
                seen.extend(x for x in v if isinstance(x, str))
    if not seen:
        return {}
    canon = {u: to_canon(c, u) for u in dict.fromkeys(seen)}         # idempotent: schon umgeschriebene Handles zurueck
    refs = {u: to_ref(c, u, viewer) for u in canon}
    for r in rows:
        pv = r.get("props") if isinstance(r.get("props"), dict) else None
        if not pv:
            continue
        for pid in pids:
            v = pv.get(pid)
            if isinstance(v, list):
                pv[pid] = [refs[x] for x in v if isinstance(x, str) and x in refs]
    lm = label_map(c, [cv for cv in canon.values() if cv], viewer)
    for u, cv in canon.items():          # unbekannte Handles (Konto geloescht): ausgegraut statt roh
        if not cv and refs[u] not in lm:
            lm[refs[u]] = {"name": t("Gelöschte Person"), "avatar": None, "me": False, "remote": False, "gone": True}
    return lm


def text_values(c, schema, rows):
    """Personen-Werte in rows (gespeicherte Kennungen) -> Anzeigenamen (Liste von Texten) - fuer Export/KI/Formeln."""
    pids = person_pids(schema)
    if not pids:
        return
    seen = [x for r in rows for pid in pids for x in ((r.get("props") or {}).get(pid) or []) if isinstance(x, str)]
    canon = {x: to_canon(c, x) for x in dict.fromkeys(seen)}
    nm = names(c, [x for x in canon.values() if x]) if seen else {}
    for r in rows:
        pv = r.get("props") if isinstance(r.get("props"), dict) else None
        if not pv:
            continue
        for pid in pids:
            v = pv.get(pid)
            if isinstance(v, list):
                pv[pid] = [nm.get(canon.get(x)) or t("Gelöschte Person") for x in v if isinstance(x, str)]


# --- Eingabe ---------------------------------------------------------------------------------------------------------
def from_ref(c, viewer, x):
    """Eingabe einer Person durch viewer -> kanonische Kennung oder '' (unbekannt). Handle '~…' (aktives lokales Konto),
    Prinzipal '@name@host' bzw. 'name@host' (fremd), sonst nur der EIGENE Benutzername (kein Orakel fuer Anmeldenamen)."""
    raw = str(x or "").strip()[:300]
    if not raw:
        return ""
    if HANDLE_RE.fullmatch(raw):
        return resolve_ref(c, raw)
    if is_remote(viewer) and raw == viewer:
        return viewer
    if not is_remote(viewer) and raw.lower() == str(viewer).lower():
        return viewer                          # eigener Benutzername (darf "@" enthalten)
    if "@" in raw.lstrip("@"):
        p = "@" + raw.lstrip("@").lower()
        if not _PRINCIPAL.fullmatch(p):
            return ""
        if FED_LOCAL is not None:              # Person DIESER Instanz (z. B. von einer anderen zurueckgeschrieben)
            mine, u = FED_LOCAL(c, p)
            if mine:
                return u or ""
        return canon_remote(c, p)
    return ""


# Von saga.federation gesetzt: fn(c, '@name@host') -> (gehoert zu dieser Instanz, Benutzername | None) - oeffentlicher
# Name oder pseudonymer Handle eines lokalen Kontos
FED_LOCAL = None


def clean_values(c, owner, nid, viewer, v, old=None, check=True):
    """Werte einer Personen-Zelle pruefen -> Liste kanonischer Kennungen (hoechstens VALUES_MAX, ohne Doppelte).
    v: Liste von Eingaben (Handles/Prinzipale/eigener Name). Schon gespeicherte Werte (old) bleiben erhalten, auch wenn
    die Person inzwischen keinen Zugriff mehr hat (Anzeige ausgegraut); neue nur mit Zugriff (allowed) - sonst ValueError."""
    if not isinstance(v, list):
        raise ValueError(t("Personen müssen eine Liste sein"))
    keep = {x: x for x in (old or []) if isinstance(x, str)}
    keep_ref = {to_ref(c, x): x for x in keep}
    out = []
    for x in v[:VALUES_MAX * 2]:
        if isinstance(x, dict):
            x = x.get("ref") or x.get("username")
        if not isinstance(x, str):
            raise ValueError(t("unbekannte Person"))
        if x in keep_ref:
            out.append(keep_ref[x])
            continue
        u = from_ref(c, viewer, x)
        if not u:
            raise ValueError(t("unbekannte Person"))
        if u in keep or u == viewer:            # sich selbst eintragen: wer bearbeitet, hat Zugriff
            out.append(u)
            continue
        if check and nid is not None and not allowed(c, owner, nid, u):
            raise ValueError(t("Diese Person hat keinen Zugriff auf die Datenbank."))
        out.append(u)
    return list(dict.fromkeys(out))[:VALUES_MAX]


# --- Auswahlliste ----------------------------------------------------------------------------------------------------
def candidates(c, owner, nid, viewer, q="", limit=CANDIDATES_MAX):
    """Wer ist hier erwaehnbar/zuweisbar (mit Zugriff), passend zur Suche q (Anzeigename, Teilwort ab 1 Zeichen - die Liste
    ist ohnehin auf Personen mit Zugriff begrenzt). -> ([{ref, name, avatar, me, remote}], mehr_vorhanden)"""
    who = with_access(c, owner, nid)
    nm = names(c, who)
    q = re.sub(r"\s+", " ", str(q or "")).strip().lower()[:60]
    hits = []
    for u in who:
        name = nm.get(u)
        if not name:
            continue
        if q and q not in name.lower() and not (is_remote(u) and q in u.lower()) and q != to_ref(c, u):
            continue
        hits.append((0 if u == viewer else 1, name.lower(), u))
    hits.sort()
    out = [{"ref": to_ref(c, u, viewer), "name": nm[u], "avatar": nm.avatar(u), "me": u == viewer, "remote": is_remote(u)}
           for _, _, u in hits[:limit]]
    return out, len(hits) > limit


def _rate_ok(uid):
    now = time.time()
    h = [x for x in _hits.get(uid, []) if now - x < 60]
    if len(h) >= RATE:
        _hits[uid] = h
        return False
    _hits[uid] = h + [now]
    if len(_hits) > 5000:
        _hits.clear()
    return True


def people_api(uid, nid, q):
    """Fuer die Weboberflaeche/Apps und die Foederation (uid = fremde Person)."""
    if not _rate_ok(uid):
        return _bad(t("zu schnell"), 429)
    with _db() as c:
        owner, _, err = access(c, uid, nid, "view")
        if err is not None:
            return err
        out, more = candidates(c, owner, nid, uid, q)
    return JSONResponse({"people": out, "more": more}, headers={"Cache-Control": "no-store"})


@router.get("/api/notes/{nid}/people")
def api_people(nid: int, request: Request, q: str = ""):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    return people_api(uid, nid, q)


# --- Zuweisungen ("mir zugewiesen") ----------------------------------------------------------------------------------
def assigned_rows(c, canon, limit=200):
    """Zeilen, in denen canon in einer Personen-Eigenschaft steht (row_person) -> [(note_id, db_id, owner, pid)].
    Nur Zeilen, die canon (noch) sehen darf - Paket 7 ("Mir zugewiesen") und Widgets bauen darauf."""
    rows = c.execute("SELECT r.note_id, r.db_id, r.owner, r.pid FROM row_person r JOIN note n ON n.id=r.note_id "
                     "WHERE r.person=? AND n.deleted_at IS NULL AND n.archived IS NULL AND n.is_template=0 ORDER BY n.updated DESC LIMIT ?",
                     (canon, limit * 2)).fetchall()
    acc = Access(c, canon)
    return [r for r in rows if r[2] == canon or acc.role(r[0])][:limit]


def drop_account(c, uid):
    """Kontoloeschung: in Personen-Werten anderer bleibt ein Grabstein ('#gone-…', Anzeige "Geloeschte Person") - ein
    spaeter gleichnamig angelegtes Konto erbt so keine Zuweisungen. row_person leert der Trigger. -> Anzahl Zeilen."""
    tomb = "#gone-" + secrets.token_hex(6)
    return _rewrite(c, uid, tomb)


def _rewrite(c, old, new):
    n = 0
    for nid, raw in c.execute("SELECT DISTINCT n.id, n.props FROM row_person r JOIN note n ON n.id=r.note_id WHERE r.person=?",
                              (old,)).fetchall():
        try:
            pv = json.loads(raw or "{}")
        except ValueError:
            continue
        ch = False
        for k, v in pv.items():
            if isinstance(v, list) and old in v:
                pv[k] = list(dict.fromkeys(new if x == old else x for x in v))
                ch = True
        if ch:
            c.execute("UPDATE note SET props=? WHERE id=?", (json.dumps(pv, ensure_ascii=False), nid))
            n += 1
    c.execute("DELETE FROM row_person WHERE person=?", (old,))
    return n


def rename_principal(c, old, new):
    """Fremde Person heisst jetzt new statt old (person.renamed): Werte in note.props (und row_person) umschreiben."""
    return _rewrite(c, old, new)
