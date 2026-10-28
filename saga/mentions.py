"""@Erwaehnungen von Personen in Seiten und Kommentaren (2.5, P6-5; Konzept P6 Abschnitt 7, Entscheidungen P6-E2/E3).

Speicherform (Vertrag P6-0, docs/APP-API.md "Erwaehnungen"): im Inhalt <span class="hk-mention" data-u="REF">Name</span>,
REF = Handle '~…' (lokales Konto, core.local_ref) bzw. Prinzipal '@name@host' (fremde Person). Der Text ist nur Rueckfall
(alte Apps, Export, Suche) - Clients zeigen den AKTUELLEN Namen aus dem Woerterbuch "people", das GET /api/notes/{id}
mitliefert (people_map). Kein Anmeldename in irgendeinem Lesepfad.

Index note_mention (db.py) - in derselben Transaktion wie das Speichern gepflegt (sync):
- Wer neu im Inhalt steht und Zugriff auf die Seite hat (people.allowed), bekommt eine Zeile. Personen ohne Zugriff
  bleiben Text im Inhalt, ohne Zeile und ohne Mitteilung (das Web bietet "Seite teilen und erwaehnen" an); bekommen sie
  spaeter Zugriff, zaehlt die naechste Speicherung.
- Mitteilung page.mention nur beim ERSTEN Erwaehnen je (Seite, Person) und entprellt: erst, wenn die Erwaehnung nach
  DELAY Sekunden noch im Inhalt steht (Tippfehler/Rueckgaengig erzeugen keine Mitteilung) - der Hintergrund-Thread
  (start/stop) verschickt faellige. Entfernen setzt gone_at; erneutes Einfuegen benachrichtigt nicht erneut.
- Still (ohne Mitteilung): sich selbst; Personen, die schon im bisherigen Stand standen (Duplikat, Vorlage, Galerie,
  Import, Version, Bestand vor 2.5 - all diese Wege schreiben den Inhalt, ohne sync aufzurufen); Verschieben (silent).
- Kommentare (Klartext) tragen Erwaehnungen als Token "<@REF>" -> comment.mention an die genannten Personen mit Zugriff
  (comment_mentions; nicht an die, die ohnehin comment.new/comment.reply fuer genau diesen Kommentar bekommen).
- Foederation (Faehigkeit ment1): Mitteilungen an fremde Personen ueber notify.create -> fed.notify; Ausgabe an andere
  Instanzen schreibt lokale Handles auf '@oeffentlicher-name@hier' um (EXT_REF, gesetzt von saga.federation), eingehend
  zurueck (in_body). Gegenstellen ohne ment1 bekommen Erwaehnungen als Text (out_body ment=False) - speichern sie zurueck,
  erhaelt pages.legacy_keep die Erwaehnungen aus dem alten Stand.
- Kontoloeschung: Erwaehnungen im Inhalt bleiben stehen (Handle loest nicht mehr auf -> "Geloeschte Person"), Zeilen weg.
- person.renamed (fremde Person): rename_principal schreibt Inhalt und Index um."""
import json
import os
import re
import threading
import time

from db import connect as _db
from saga import notify, people
from saga.core import HANDLE_RE, local_ref, resolve_ref, t
from saga.notes import _iter_blocks
from saga.sharing import is_remote, names, remote_label

DELAY = float(os.environ.get("SAGA_MENTION_DELAY", "20"))       # Sekunden bis zur Mitteilung (Tests kuerzer)
PER_PAGE = 200                                                  # Erwaehnungen je Seite (mehr werden nicht indiziert)
_SPAN = re.compile(r'<span class="hk-mention" data-u="([^"]{1,300})">(.*?)</span>', re.S)
_REF = re.compile(r"~[a-z2-7]{16}|@(?:[a-z0-9][a-z0-9_-]{1,31}|~[a-z2-7]{16})@[a-z0-9.:-]{1,260}")
_TOKEN = re.compile(r"<@(~[a-z2-7]{16}|@(?:[a-z0-9][a-z0-9_-]{1,31}|~[a-z2-7]{16})@[a-z0-9.:-]{1,260})>")
# Von saga.federation gesetzt: fn(c, uid, peer) -> '@name@hier' (oeffentlicher Name / pseudonymer Handle) oder None
EXT_REF = None


# --- Lesen der Kennungen -----------------------------------------------------------------------------------------------
def _strings(v, out):
    """Textfelder eines Blocks (ohne verschachtelte Bloecke - die zaehlen fuer sich)."""
    if isinstance(v, str):
        out.append(v)
    elif isinstance(v, list):
        for x in v:
            _strings(x, out)
    elif isinstance(v, dict):
        for k, x in v.items():
            if k not in ("blocks", "cols"):
                _strings(x, out)


def refs_in(body, fmt="blocks"):
    """Erwaehnungen im Inhalt -> {ref: block_id} (erster Block je Person)."""
    if fmt != "blocks" or not body or "hk-mention" not in body:
        return {}
    try:
        doc = json.loads(body)
        blocks = list(_iter_blocks(doc.get("blocks") or []))
    except (ValueError, AttributeError, RecursionError):
        return {}
    out = {}
    for b in blocks:
        ss = []
        _strings(b.get("data"), ss)
        for s in ss:
            if "hk-mention" in s:
                for m in _SPAN.finditer(s):
                    if _REF.fullmatch(m.group(1)):
                        out.setdefault(m.group(1), b.get("id") if isinstance(b.get("id"), str) else "")
        if len(out) >= PER_PAGE:
            break
    return out


def canon_of(c, ref):
    """Kennung aus dem Inhalt -> kanonische Person (Benutzername bzw. Prinzipal) oder '' (unbekannt/geloescht)."""
    if not isinstance(ref, str):
        return ""
    if HANDLE_RE.fullmatch(ref):
        return resolve_ref(c, ref)
    if _REF.fullmatch(ref) and ref.startswith("@"):
        if people.FED_LOCAL is not None:
            mine, u = people.FED_LOCAL(c, ref)
            if mine:
                return u or ""
        return people.canon_remote(c, ref)
    return ""


def people_map(c, refs, viewer, remote=False):
    """{ref: {name, avatar, me, remote, gone}} fuer die Erwaehnungen refs aus Sicht von viewer (Name nie der Anmeldename).
    remote=True (Antwort an eine andere Instanz): nur name/gone."""
    out = {}
    canon = {r: canon_of(c, r) for r in dict.fromkeys(refs)}
    nm = names(c, [x for x in canon.values() if x])
    for r, u in canon.items():
        if u and u in nm:
            e = {"name": nm[u], "avatar": nm.avatar(u), "me": u == viewer, "remote": is_remote(u)}
        else:
            e = {"name": t("Gelöschte Person"), "avatar": None, "me": False, "remote": is_remote(r), "gone": True}
        out[r] = {"name": e["name"], "gone": bool(e.get("gone"))} if remote else e
    return out


# --- Speichern ---------------------------------------------------------------------------------------------------------
def normalize(c, body):
    """Beim Speichern: Prinzipale von Personen DIESER Instanz -> Handle; fremde in ihre gefuehrte Form (wie Freigaben);
    unbekannte/geloeschte bleiben, wie sie sind (Anzeige "Geloeschte Person")."""
    if not body or "hk-mention" not in body or people.FED_LOCAL is None:
        return body

    def rep(m):
        ref = m.group(1)
        if not ref.startswith("@"):
            return m.group(0)
        mine, u = people.FED_LOCAL(c, ref)
        new = (local_ref(u) if u else None) if mine else people.canon_remote(c, ref)
        if not new or new == ref:
            return m.group(0)
        return f'<span class="hk-mention" data-u="{new}">{m.group(2)}</span>'
    try:
        doc = json.loads(body)
    except ValueError:
        return body
    ch = [False]

    def walk(v):
        if isinstance(v, str):
            if "hk-mention" in v:
                n = _SPAN.sub(rep, v)
                if n != v:
                    ch[0] = True
                return n
            return v
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        return v
    doc = walk(doc)
    return json.dumps(doc, ensure_ascii=False) if ch[0] else body


def strip_foreign(c, owner, nid, old_body, body):
    """Beim Speichern (vor dem Schreiben): Erwaehnungen von Personen OHNE Zugriff auf die Seite, die nicht schon im
    bisherigen Stand standen, werden zu Text (P6-E3: erwaehnen nur mit Zugriff; "Seite teilen und erwaehnen" teilt
    zuerst). So laesst sich ueber eine Erwaehnung auch kein Name eines beliebigen Handles erfragen."""
    if not body or "hk-mention" not in body:
        return body
    old = set(refs_in(old_body))
    ok = {}

    def rep(m):
        ref, txt = m.group(1), m.group(2)
        if ref in old:
            return m.group(0)
        if ref not in ok:
            u = canon_of(c, ref)
            ok[ref] = bool(u) and (u == owner or people.allowed(c, owner, nid, u))
        return m.group(0) if ok[ref] else txt
    return _rewrite(body, rep)


def sync(c, owner, nid, old_body, new_body, actor, silent=False, old_fmt="blocks", new_fmt="blocks"):
    """note_mention fuer nid nach dem Speichern abgleichen (Transaktion von c). -> Anzahl neu vorgemerkter Mitteilungen."""
    now = time.time()
    new = refs_in(new_body, new_fmt)
    old = refs_in(old_body, old_fmt)
    rows = {r[0]: r[1:] for r in c.execute("SELECT person, gone_at, notified FROM note_mention WHERE note_id=?", (nid,)).fetchall()}
    old_canon = {canon_of(c, r) for r in old}
    seen, queued = set(), 0
    for ref, bid in new.items():
        u = canon_of(c, ref)
        if not u or u in seen:
            continue
        seen.add(u)
        if u in rows:
            if rows[u][0] is not None:
                c.execute("UPDATE note_mention SET gone_at=NULL, block_id=? WHERE note_id=? AND person=?", (bid or "", nid, u))
            continue
        if not people.allowed(c, owner, nid, u):
            continue                                   # ohne Zugriff: nur Text (kein Index, keine Mitteilung)
        quiet = silent or u == actor or u in old_canon
        c.execute("INSERT INTO note_mention (note_id, person, block_id, first_by, first_at, notified) VALUES (?,?,?,?,?,?) "
                  "ON CONFLICT (note_id, person) DO NOTHING", (nid, u, bid or "", actor, now, now if quiet else None))
        queued += 0 if quiet else 1
    for u, (gone, _n) in rows.items():
        if u not in seen and gone is None:
            c.execute("UPDATE note_mention SET gone_at=? WHERE note_id=? AND person=?", (now, nid, u))
    if queued:
        c.after_commit(_wake.set)
    return queued


# --- Mitteilungen ------------------------------------------------------------------------------------------------------
def dispatch(now=None):
    """Faellige Erwaehnungen mitteilen (noch im Inhalt, DELAY abgelaufen, Seite da, Person hat Zugriff). -> Anzahl."""
    now = now or time.time()
    n = 0
    with _db() as c, c:
        rows = c.execute("SELECT m.note_id, m.person, m.block_id, m.first_by, n.uid FROM note_mention m JOIN note n ON n.id=m.note_id "
                         "WHERE m.notified IS NULL AND m.gone_at IS NULL AND m.first_at<=? AND n.deleted_at IS NULL "
                         "ORDER BY m.first_at LIMIT 200 FOR UPDATE OF m SKIP LOCKED", (now - DELAY,)).fetchall()
        for nid, u, bid, by, owner in rows:
            c.execute("UPDATE note_mention SET notified=? WHERE note_id=? AND person=?", (now, nid, u))
            if u == by or not (u == owner or people.allowed(c, owner, nid, u)):
                continue
            notify.create(c, u, "page.mention", by, nid, data={"block": bid or ""})
            n += 1
    return n


def comment_targets(c, owner, nid, body, author):
    """Kommentar mit Token "<@REF>" -> Personen mit Zugriff, die comment.mention bekommen (nicht die schreibende Person)."""
    out = []
    for ref in list(dict.fromkeys(_TOKEN.findall(body or "")))[:20]:
        u = canon_of(c, ref)
        if not u or u == author or u in out:
            continue
        if u == owner or people.allowed(c, owner, nid, u):
            out.append(u)
    return out


def _comment_mentioned(c, owner, note_id, comment_id, author):
    """notify.COMMENT_MENTIONED: wer ist in diesem (neuen) Kommentar erwaehnt?"""
    r = c.execute("SELECT body FROM note_comment WHERE id=?", (comment_id,)).fetchone()
    return comment_targets(c, owner, note_id, r[0], author) if r and r[0] and "<@" in r[0] else []


def comment_people(c, bodies, viewer):
    """Woerterbuch {ref: {...}} fuer die Token in Kommentaren (Anzeige als Chip)."""
    refs = [r for b in bodies for r in _TOKEN.findall(b or "")]
    return people_map(c, refs, viewer) if refs else {}


def comment_text(c, body):
    """Kommentar fuer Texte (Mitteilungs-Auszug, Export): Token -> '@Name'."""
    if not body or "<@" not in body:
        return body
    refs = _TOKEN.findall(body)
    if not refs:
        return body
    pm = people_map(c, refs, None)
    return _TOKEN.sub(lambda m: "@" + pm.get(m.group(1), {}).get("name", "?"), body)


notify.COMMENT_MENTIONED = _comment_mentioned
notify.COMMENT_TEXT = lambda c, body: comment_text(c, body)


# --- Foederation -------------------------------------------------------------------------------------------------------
def out_body(c, body, peer=None, ment=True):
    """Inhalt fuer eine andere Instanz: lokale Handles -> '@name@hier' (EXT_REF), ohne oeffentlichen Namen bzw. ohne
    Faehigkeit ment1 (ment=False) -> nur der Text. Fremde Prinzipale bleiben."""
    if not body or "hk-mention" not in body:
        return body
    cache = {}

    def rep(m):
        ref, txt = m.group(1), m.group(2)
        if not ment:
            return txt
        if HANDLE_RE.fullmatch(ref):
            if ref not in cache:
                u = resolve_ref(c, ref)
                cache[ref] = EXT_REF(c, u, peer) if (u and EXT_REF is not None) else None
            ext = cache[ref]
            if not ext:
                return txt
            return f'<span class="hk-mention" data-u="{ext}">{txt}</span>'
        return m.group(0)
    return _rewrite(body, rep)


def in_body(c, body, self_host, local_by_name):
    """Inhalt einer anderen Instanz fuer die Anzeige hier: Prinzipale dieser Instanz -> lokale Handles, fremde bleiben
    (in ihrer gefuehrten Form). local_by_name(c, name) -> Benutzername oder None."""
    if not body or "hk-mention" not in body:
        return body

    def rep(m):
        ref, txt = m.group(1), m.group(2)
        mm = re.fullmatch(r"@([^@]+)@(.+)", ref)
        if not mm:
            return txt                               # fremde Handles ('~…') gelten nur dort
        if mm.group(2) == self_host:
            u = local_by_name(c, mm.group(1))
            return f'<span class="hk-mention" data-u="{local_ref(u)}">{txt}</span>' if u else txt
        cr = people.canon_remote(c, ref)
        return f'<span class="hk-mention" data-u="{cr}">{txt}</span>'
    return _rewrite(body, rep)


def home_out(c, body, ext_of):
    """Speichern einer fremden Seite (Heim -> Eigentuemer): lokale Handles -> '@name@hier' (ext_of(uid) oder None = Text)."""
    if not body or "hk-mention" not in body:
        return body

    def rep(m):
        ref, txt = m.group(1), m.group(2)
        if HANDLE_RE.fullmatch(ref):
            u = resolve_ref(c, ref)
            ext = ext_of(u) if u else None
            return f'<span class="hk-mention" data-u="{ext}">{txt}</span>' if ext else txt
        return m.group(0)
    return _rewrite(body, rep)


def remote_people(c, refs, pm_in):
    """Heim: Woerterbuch fuer Erwaehnungen auf einer fremden Seite (lokale Namen von hier, fremde mit Herkunft)."""
    out = people_map(c, refs, None)
    for r in refs:
        if is_remote(r):
            e = (pm_in or {}).get(r) if isinstance(pm_in, dict) else None
            nm = e.get("name") if isinstance(e, dict) and isinstance(e.get("name"), str) else None
            out[r] = dict(out.get(r) or {}, name=remote_label(r, (nm or "")[:120] or None), remote=True, gone=False)
    return out


def _rewrite(body, rep):
    try:
        doc = json.loads(body)
    except ValueError:
        return body

    def walk(v):
        if isinstance(v, str):
            return _SPAN.sub(rep, v) if "hk-mention" in v else v
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        return v
    return json.dumps(walk(doc), ensure_ascii=False)


def rename_principal(c, old, new):
    """Fremde Person heisst jetzt new (person.renamed): Index und Inhalt der Seiten, in denen sie erwaehnt ist."""
    n = 0
    for (nid,) in c.execute("SELECT note_id FROM note_mention WHERE person=?", (old,)).fetchall():
        r = c.execute("SELECT body FROM note WHERE id=?", (nid,)).fetchone()
        if r and r[0] and old in r[0]:
            nb = _rewrite(r[0], lambda m: f'<span class="hk-mention" data-u="{new}">{m.group(2)}</span>' if m.group(1) == old else m.group(0))
            if nb != r[0]:
                c.execute("UPDATE note SET body=? WHERE id=?", (nb, nid))
                n += 1
        if c.execute("SELECT 1 FROM note_mention WHERE note_id=? AND person=?", (nid, new)).fetchone():
            c.execute("DELETE FROM note_mention WHERE note_id=? AND person=?", (nid, old))
        else:
            c.execute("UPDATE note_mention SET person=? WHERE note_id=? AND person=?", (new, nid, old))
    return n


def drop_account(c, uid):
    """Kontoloeschung: Index-Zeilen der Person weg (der Inhalt bleibt - der Handle loest nicht mehr auf)."""
    return c.execute("DELETE FROM note_mention WHERE person=?", (uid,)).rowcount


# --- Hintergrund -------------------------------------------------------------------------------------------------------
_wake = threading.Event()
_thread = {"t": None, "stop": False}


def _loop():
    while not _thread["stop"]:
        try:
            dispatch()
        except Exception as ex:  # noqa: BLE001 - naechster Durchlauf
            print(f"mentions: {type(ex).__name__}: {ex}", flush=True)
        _wake.wait(max(0.5, min(DELAY / 2, 10.0)))     # gerade vorgemerkte sind erst nach DELAY faellig (naechste Runden)
        _wake.clear()


def start():
    if _thread["t"] is None or not _thread["t"].is_alive():
        _thread["stop"] = False
        _thread["t"] = threading.Thread(target=_loop, name="saga-mentions", daemon=True)
        _thread["t"].start()


def stop():
    _thread["stop"] = True
    _wake.set()
