"""Teilen (1.3): Freigaben einer Seite an Konten und Gruppen, die zentrale Zugriffspruefung und die Personensuche.

Rollen: owner (Eigentuemer) > edit (Bearbeiten) > comment (Kommentieren) > view (Lesen) > keine.
- Eine Freigabe gilt fuer die Seite und alle Unterseiten/Eintraege (Vererbung ueber parent_id); es zaehlt die hoechste
  Rolle in der Kette. Die Kette endet vor geloeschten/archivierten Seiten und Vorlagen (notes.note_chain, wie beim
  Veroeffentlichen) - solche Seiten sieht nur der Eigentuemer.
- Gruppen: lokale (user_group) und aus LDAP/OIDC (user_ext_group), immer frisch aus der Datenbank gelesen -
  Gruppenwechsel und Entzug wirken mit der naechsten Anfrage.
- Endpunkte antworten ohne Rolle mit 404 (die Seite "gibt es nicht"), mit zu kleiner Rolle mit 403.
- Fuer Listen (Seitenleiste "Mit mir geteilt", Suche, Export) gibt es Access.tree(): eine Abfrage je Eigentuemer statt
  einer Kette je Seite.
- Foederation (2.0): Personen fremder Instanzen sind "Prinzipale" '@name@host' (oeffentlicher Name + Host). Sie koennen
  nie ein lokaler Benutzername sein (die beginnen mit a-z/0-9). Freigaben an sie: target_type 'remote' ('@name@host')
  bzw. 'remote_group' ('gruppe@host'). Ihre Gruppen bestaetigt der Heimatserver signiert pro Anfrage (REMOTE_GROUPS,
  gesetzt nur von saga.federation nach geprueften Signaturen)."""
import contextvars
import json
import re
import threading
import time
from html import escape as _esc
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
import auth
from db import connect as _db
from saga import avatars, bus, i18n
from saga.core import RESTORE_TEST, _api_guard, _bad, _body, labels, ref_of, resolve_ref, t
from saga.notes import NOTE_META_COLS, _note_meta, note_chain

router = APIRouter()

ROLES = ("view", "comment", "edit")
RANK = {None: 0, "view": 1, "comment": 2, "edit": 3, "owner": 4}
SHARE_MAX = 200                                  # Freigaben je Seite
TARGETS_MAX = 10                                 # Treffer der Personensuche
REMOTE_TYPES = ("remote", "remote_group")
# {prinzipal: {"gruppe@host", ...}} - die vom Heimatserver signiert bestaetigten Gruppen der anfragenden fremden Person
REMOTE_GROUPS = contextvars.ContextVar("saga_remote_groups", default=None)
# Von saga.federation gesetzt: remote_target(uid, typ, ziel) -> (normalisiertes ziel, fehlertext, status) - prueft
# Verbindung und Person/Gruppe bei der fremden Instanz (Netzwerk, ausserhalb jeder Transaktion aufrufen)
REMOTE_TARGET = None
REMOTE_SEARCH = None          # remote_search(uid, q) -> ([{type, target, name, host}], hinweis|None) (Personensuche, genaue Adresse name@host)
# 2.3 (saga.federation / saga.fed_dir): Ausnahme je Instanz von "nur gemeinsame Gruppen" und Adressbuch-Suche
FED_SCOPE_OK = None           # scope_ok(host|None) -> bool: Instanz (bzw. irgendeine) hat die Admin-Ausnahme
DIR_SEARCH = None             # dir_search(uid, q, zweck) -> [{type: 'remote', target, name, host, label, avatar}]


def is_remote(uid):
    """Fremde Person ('@name@host')? Lokale Benutzernamen beginnen nie mit '@'."""
    return isinstance(uid, str) and uid.startswith("@")


def remote_label(principal, display=None):
    """'@bert@b.example' + 'Bert' -> 'Bert (bert@b.example)' - fremde Namen immer mit Herkunft (keine Verwechslung).
    Pseudonymer Handle (2.3, '@~x@host'): 'Bert (b.example)' - die Zufallskennung sagt Menschen nichts."""
    h = principal[1:]
    if h.startswith("~") and "@" in h:
        h = h.split("@", 1)[1]
    d = re.sub(r"[\x00-\x1f\x7f]", "", str(display or "")).strip()
    return f"{d} ({h})" if d else principal[1:]


def enabled():
    return bool(auth.settings().get("share_enabled", True))


def fed_enabled():
    return not RESTORE_TEST and bool(auth.settings().get("fed_enabled", False))


def group_scope():
    return bool(auth.settings().get("share_group_scope", False))


def fed_share_ok(target=None):
    """Teilen ueber Instanzgrenzen erlaubt? "Nur gemeinsame Gruppen" sperrt es - ausser fuer Instanzen mit der
    Admin-Ausnahme (2.3). target: 'name@host' / '@name@host' (None: mit irgendeiner Instanz)."""
    if not group_scope():
        return True
    host = str(target).rpartition("@")[2].strip().lower() if target and "@" in str(target).lstrip("@") else None
    return FED_SCOPE_OK is not None and bool(FED_SCOPE_OK(host))


def _best(a, b):
    return a if RANK.get(a, 0) >= RANK.get(b, 0) else b


def groups_of(c, uid):
    """Gruppen eines Kontos (lokal + LDAP/OIDC), frisch aus der Datenbank. Fremde Person: nur die in DIESER Anfrage
    signiert bestaetigten Gruppen ('gruppe@host')."""
    if is_remote(uid):
        return set((REMOTE_GROUPS.get() or {}).get(uid, ()))
    return {r[0] for r in c.execute(
        "SELECT g.name FROM user_group ug JOIN groups g ON g.id=ug.group_id JOIN users u ON u.id=ug.user_id WHERE u.username=? "
        "UNION SELECT e.name FROM user_ext_group e JOIN users u ON u.id=e.user_id WHERE u.username=?", (uid, uid))}


class Names(dict):
    """Ergebnis von names(): Benutzername -> Anzeigename; .av: Benutzername -> Profilbild-Adresse (saga.avatars)."""
    av = None

    def avatar(self, uid):
        return (self.av or {}).get(uid)


def names(c, uids):
    """Benutzername -> Name zum Zeigen (core.labels). Unbekannte/geloeschte fehlen. Profilbilder gleich mit (.av)."""
    uids = [u for u in set(uids) if u]
    out = Names()
    out.av = {}
    if not uids:
        return out
    loc = [u for u in uids if not is_remote(u)]
    rem = [u for u in uids if is_remote(u)]
    if loc:          # 2.4 (W4): nie der Anmeldename - Anzeigename, oeffentlicher Name oder pseudonymer Handle
        out.update(labels(c, loc))
    if rem:          # fremde Personen: Anzeigename aus dem Zwischenspeicher, immer mit Herkunft
        disp = {r[0]: r[1] for r in c.execute(f"SELECT principal, display FROM fed_person WHERE principal IN ({','.join('?' * len(rem))})",
                                              tuple(rem))}
        out.update({u: remote_label(u, disp.get(u)) for u in rem})
    out.av = avatars.urls(c, list(out))
    return out


def person(c, uid, me, cache=None):
    """{name, username, me, avatar} fuer Eigentuemer/Verfasser; uid None/geloescht -> 'Geloeschtes Konto'.
    Fremde Person: zusaetzlich remote=true, username = '@name@host'. avatar: Adresse des Profilbilds oder None (Initialen)."""
    cache = cache if cache is not None else names(c, [uid]) if uid else {}
    nm = cache.get(uid) if uid else None
    if not nm:
        return {"name": t("Gelöschtes Konto"), "username": None, "me": False, "avatar": None}
    av = cache.avatar(uid) if isinstance(cache, Names) else (avatars.url_of(c, uid) if c is not None else None)
    if is_remote(uid):
        return {"name": nm, "username": uid, "me": uid == me, "remote": True, "avatar": av}
    return {"name": nm, "username": ref_of(c, uid, me), "me": uid == me, "avatar": av}     # 2.4: andere als Handle


# --- Zwischenspeicher fuer Access.tree (2.2) ------------------------------------------------------------------------
# Der Freigabe-Baum haengt nur an den Seiten des Eigentuemers (Spalten, die der Abgleich-Trigger in db.TREE_SEQ_SQL
# beobachtet: Titel, Symbol, Eltern, Papierkorb, Archiv, Vorlage, Art, sort, created, updated, Schlagworte, angeheftet)
# und an den Freigabe-Wurzeln mit Rolle (Teil des Schluessels - Freigaben und Gruppen werden je Anfrage frisch gelesen).
# Gueltig ist ein Eintrag, solange es keine Aenderung an Seiten des Eigentuemers gibt, die der Leser nicht sehen konnte:
# jede solche Transaktion hat eine Nummer >= xmin seines Datenstands und hinterlaesst eine Zeile in note_tseq (in
# derselben Transaktion wie die Aenderung). Nur in READ COMMITTED (jede Anweisung sieht mindestens den Stand des
# Eintrags), nur ohne eigene Schreibvorgaenge der Transaktion (nie einen unbestaetigten Stand merken), dieselbe Kennung
# (epoch, neu je Start/Backup). Im Zweifel (Tabelle fehlt, Fehler): kein Zwischenspeicher, Abfrage wie bisher.
TREE_CACHE_ROWS = 150000          # Zeilen insgesamt (aelteste Eintraege fliegen zuerst)
TREE_CACHE_AGE = 300              # Sekunden; danach neu (Grabsteine in note_tseq werden nach 30 Tagen geloescht)
_TREE_CACHE = {}                  # key -> (epoch, xmin, zeit, anzahl, rollen, mit_zeilen); zuletzt benutzt zuletzt
_TREE_LOCK = threading.Lock()


def _tree_cached(c, key):
    """-> ((rollen, mit_zeilen) | None, stand fuer _tree_store | None). Die Woerterbuecher nur lesen (kopieren)."""
    with _TREE_LOCK:
        ent = _TREE_CACHE.get(key)
    try:
        c.execute("SAVEPOINT tree_cache")
        r = c.execute("SELECT (SELECT v FROM note_tseq_meta WHERE k='epoch'), pg_snapshot_xmin(pg_current_snapshot())::text::bigint, "
                      "current_setting('transaction_isolation') = 'read committed' AND pg_current_xact_id_if_assigned() IS NULL, "
                      "EXISTS (SELECT 1 FROM note_tseq WHERE uid=? AND xid >= ?)",
                      (key[0], ent[1] if ent else 2 ** 62)).fetchone()
        c.execute("RELEASE SAVEPOINT tree_cache")
    except Exception:              # kein Abgleich-Stand (Postgres < 13, Rechte): immer frisch abfragen
        c.execute("ROLLBACK TO SAVEPOINT tree_cache")
        return None, None
    epoch, xmin, clean, changed = r
    if not epoch or not clean:
        return None, None
    if ent and ent[0] == epoch and not changed and time.monotonic() - ent[2] < TREE_CACHE_AGE:
        with _TREE_LOCK:
            if _TREE_CACHE.get(key) is ent:
                _TREE_CACHE[key] = _TREE_CACHE.pop(key)          # zuletzt benutzt
        return ent[4:], None
    return None, (epoch, int(xmin), time.monotonic())


def _tree_store(key, st, roles, full):
    if st is None or len(roles) > TREE_CACHE_ROWS:
        return
    with _TREE_LOCK:
        old = _TREE_CACHE.pop(key, None)
        if old is not None and old[1] > st[1]:          # ein neuerer Stand ist schon da
            _TREE_CACHE[key] = old
            return
        _TREE_CACHE[key] = (*st, max(1, len(roles)), roles, full)       # leere Baeume zaehlen mit (sonst unbegrenzt viele)
        total = sum(e[3] for e in _TREE_CACHE.values())
        while total > TREE_CACHE_ROWS:
            k = next(iter(_TREE_CACHE))
            total -= _TREE_CACHE.pop(k)[3]


class Access:
    """Zugriff EINES Kontos auf Seiten - je Anfrage einmal anlegen; Eigentuemer, Freigaben, Gruppen und Rollen werden
    zwischengespeichert (kurzer Cache je Anfrage)."""

    def __init__(self, c, uid):
        self.c, self.uid = c, uid
        self._on, self._groups = None, None
        self._owner, self._role, self._shares, self._tree = {}, {}, {}, {}

    def on(self):
        if self._on is None:          # fremde Personen zusaetzlich nur bei eingeschalteter Foederation
            self._on = enabled() and (not is_remote(self.uid) or fed_enabled())
        return self._on

    def groups(self):
        if self._groups is None:
            self._groups = groups_of(self.c, self.uid)
        return self._groups

    def owner(self, nid):
        """Eigentuemer einer Seite (auch im Papierkorb) oder None."""
        if nid not in self._owner:
            r = self.c.execute("SELECT uid FROM note WHERE id=?", (nid,)).fetchone() if 0 < nid < 2 ** 63 else None
            self._owner[nid] = r[0] if r else None
        return self._owner[nid]

    def _principals(self):
        g = sorted(self.groups())
        tu, tg = ("remote", "remote_group") if is_remote(self.uid) else ("user", "group")
        sql = f"((target_type='{tu}' AND target=?)" + (f" OR (target_type='{tg}' AND target IN ({','.join('?' * len(g))}))" if g else "") + ")"
        return sql, (self.uid, *g)

    def shares_from(self, owner):
        """{note_id: rolle} aller Freigaben, die owner mir (direkt oder ueber meine Gruppen) gegeben hat."""
        if owner not in self._shares:
            sql, args = self._principals()
            out = {}
            for nid, role in self.c.execute(f"SELECT note_id, role FROM note_share WHERE owner_uid=? AND {sql}", (owner, *args)):
                out[nid] = _best(out.get(nid), role)
            self._shares[owner] = out
        return self._shares[owner]

    def owners(self):
        """Konten, die mit mir etwas geteilt haben."""
        if not self.on():
            return []
        sql, args = self._principals()
        return sorted({r[0] for r in self.c.execute(f"SELECT DISTINCT owner_uid FROM note_share WHERE {sql}", args)} - {self.uid})

    def role(self, nid):
        """'owner' | 'edit' | 'comment' | 'view' | None"""
        if nid in self._role:
            return self._role[nid]
        o, r = self.owner(nid), None
        if o is None:
            r = None
        elif o == self.uid:
            r = "owner"
        elif self.on():
            sh = self.shares_from(o)
            if sh:
                if o in self._tree:
                    r = self._tree[o].get(nid)
                else:
                    for i in note_chain(self.c, o, nid):
                        r = _best(r, sh.get(i))
        self._role[nid] = r
        return r

    def tree(self, owner, meta=False):
        """Alle Seiten von owner, die ich sehen darf: {id: rolle} (meta=True: {id: (rolle, zeile)}), in Baum-Reihenfolge
        (sort, created). EINE Abfrage: rekursiv von meinen Freigaben abwaerts (Index note_parent) statt des ganzen Baums
        des Eigentuemers. Vererbung wie note_chain: jede Seite bekommt die hoechste Rolle der Freigaben auf ihr und
        darueber; geloeschte/archivierte Seiten und Vorlagen unterbrechen die Kette (sie selbst und alles darunter, was
        nicht unterhalb von ihnen eigens freigegeben ist, bleibt unsichtbar)."""
        if owner == self.uid or not self.on():
            return {}
        sh = self.shares_from(owner)
        if not sh:
            self._tree[owner] = {}
            return {}
        mc = [x.strip() for x in NOTE_META_COLS.split(",") if x.strip() != "id"] if meta else []
        tcols = "".join(f", n.{x}" for x in mc)                 # Zeilen-Spalten gleich mitfuehren: kein zweiter Zugriff
        ok = "n.uid=? AND n.deleted_at IS NULL AND n.archived IS NULL AND n.is_template=0"
        roots = sorted(i for i in sh if RANK.get(sh[i], 0) > 0)
        ranks = [RANK[sh[i]] for i in roots]
        key = (owner, bool(meta), tuple(roots), tuple(ranks))
        hit, st = _tree_cached(self.c, key)
        if hit is None:
            # Jede Freigabe vererbt abwaerts; ueberlappen sich Freigaben (Seite und Unterseite freigegeben), kommt eine
            # Seite mehrfach an - es gilt die hoechste Rolle (DISTINCT ON ... r DESC)
            rows = self.c.execute(
                "WITH RECURSIVE t AS ("
                f" SELECT n.id, x.r, 0 AS depth, n.sort, n.created{tcols} FROM unnest(?::bigint[], ?::int[]) AS x(id, r)"
                f" JOIN note n ON n.id=x.id WHERE {ok}"
                f" UNION ALL SELECT n.id, t.r, t.depth + 1, n.sort, n.created{tcols} FROM t JOIN note n ON n.parent_id=t.id"
                f" WHERE {ok} AND t.depth < 10000)"
                f" SELECT id, r{''.join(', ' + x for x in (['id'] + mc if meta else []))}"
                " FROM (SELECT DISTINCT ON (id) * FROM t ORDER BY id, r DESC) v ORDER BY sort NULLS FIRST, created, id",
                (roots, ranks, owner, owner)).fetchall()
            name = {v: k for k, v in RANK.items() if k}
            roles = {r[0]: name[r[1]] for r in rows}
            full = {r[0]: (name[r[1]], r[2:]) for r in rows} if meta else None
            _tree_store(key, st, roles, full)
        else:
            roles, full = hit
        roles = dict(roles)                                # eigene Kopie (der Zwischenspeicher bleibt, wie er ist)
        self._tree[owner] = roles
        merged = dict(roles)                               # = setdefault je Seite: schon bekannte Rollen bleiben
        merged.update(self._role)
        self._role = merged
        if meta:
            return dict(full)
        return roles


def access(c, uid, nid, minimum="view", acc=None):
    """Zentrale Pruefung fuer Endpunkte -> (eigentuemer, rolle, fehlerantwort). Fehler: 404 ohne Rolle, 403 bei zu kleiner Rolle."""
    acc = acc or Access(c, uid)
    r = acc.role(nid)
    if not r:
        return None, None, _bad("not found", 404)
    if RANK[r] < RANK[minimum]:
        return acc.owner(nid), r, _bad(t("Dafür fehlt dir die Berechtigung."), 403)
    return acc.owner(nid), r, None


def note_access(c, uid, nid):
    """Rolle eines Kontos fuer eine Seite: 'owner' | 'edit' | 'comment' | 'view' | None."""
    return Access(c, uid).role(nid)


def shared_ids(c, owner):
    """Seiten von owner, die selbst (nicht nur geerbt) freigegeben sind - fuer das Symbol im Seitenbaum."""
    return {r[0] for r in c.execute("SELECT DISTINCT note_id FROM note_share WHERE owner_uid=?", (owner,))}


def visible_pages(c, uid, acc=None):
    """Alle fuer mich freigegebenen fremden Seiten: [(eigentuemer, rolle, meta-zeile, wurzel?)] - Mengen-Abfrage je Eigentuemer."""
    acc = acc or Access(c, uid)
    out = []
    for o in acc.owners():
        tr = acc.tree(o, meta=True)
        for nid, (role, row) in tr.items():
            out.append((o, role, row, row[3] not in tr))
    return out


# --- Verweise auf Seiten ohne Zugriff ----------------------------------------------------------------
# Im gespeicherten Inhalt steht ein Seitenverweis als <a href="/notes#ID">Text</a> (pages._Sanitizer). Der Text ist meist
# der Titel der Zielseite. Nicht-Eigentuemer bekommen fuer Ziele, die sie nicht sehen duerfen, einen neutralen Text -
# in jeder Antwort (Seite, Suche, Export). Speichert jemand mit 'Bearbeiten', setzt restore_links die Originaltexte
# wieder ein (Zuordnung ueber Block-id, Ziel-ID und Reihenfolge); geloeschte Verweise bleiben geloescht.
# gespeichert (sanitize_inline) immer genau <a href="/notes#ID">; weitere Attribute nur zur Sicherheit erlaubt
_NOTE_LINK = re.compile(r'<a\s[^>]*?\bhref="/notes#([1-9][0-9]{0,17})"[^>]*>(.*?)</a>', re.S | re.I)


def _walk_strings(v, fn, key=None, blk=None):
    """Alle Texte eines Block-Dokuments (auch verschachtelt) durch fn(text, block_id) schicken - ausser Code (woertlich).
    block_id: id des naechsten umgebenden Blocks (Editor.js; auch in Toggle/Spalten) oder None."""
    if isinstance(v, str):
        return v if key == "code" else fn(v, blk)
    if isinstance(v, list):
        return [_walk_strings(x, fn, key, blk) for x in v]
    if isinstance(v, dict):
        if isinstance(v.get("id"), str) and v["id"] and isinstance(v.get("data"), dict):
            blk = v["id"]
        return {k: _walk_strings(x, fn, k, blk) for k, x in v.items()}
    return v


def _load_doc(body):
    try:
        d = json.loads(body or "")
        return d if isinstance(d, dict) else None
    except ValueError:
        return None


def mask_links(body, fmt, visible):
    """Inhalt fuer einen Nicht-Eigentuemer: Texte von Verweisen auf Seiten ohne Zugriff -> 'Seite ohne Zugriff'.
    visible(id) -> bool."""
    if fmt != "blocks" or "/notes#" not in (body or ""):
        return body
    d = _load_doc(body)
    if d is None:
        return body
    na = _esc(t("Seite ohne Zugriff"), quote=False)
    hit = [False]

    def fn(s, _blk=None):
        def rep(m):
            if visible(int(m.group(1))):
                return m.group(0)
            hit[0] = True
            return f'<a href="/notes#{m.group(1)}">{na}</a>'
        return _NOTE_LINK.sub(rep, s) if "/notes#" in s else s
    out = _walk_strings(d, fn)
    return json.dumps(out, ensure_ascii=False) if hit[0] else body


def _na_texts():
    """Der Platzhalter in allen Sprachen (so, wie er nach dem Bereinigen im Inhalt steht)."""
    return {_esc(cat.get("Seite ohne Zugriff") or "Seite ohne Zugriff", quote=False) for cat in i18n.CATALOG.values()}


def restore_links(old_body, new_body, visible, title_of=None):
    """Speichern durch Nicht-Eigentuemer: Verweise auf Seiten ohne Zugriff bekommen ihren alten Text zurueck.
    Zuordnung zuerst im selben Block (Block-id, k-ter Verweis auf ID x dort = k-ter vorher - Bloecke verschieben oder Text
    drumherum aendern vertauscht nichts), sonst ueber das ganze Dokument; mehr als vorher: der letzte alte Text.
    Ein kopierter Platzhalter (Verweis, den es vorher auf dieser Seite nicht gab) bekommt den aktuellen Titel des Ziels -
    title_of(id) liefert ihn nur fuer Seiten desselben Eigentuemers (sonst None: Platzhalter bleibt). Der Titel landet so
    nur beim Eigentuemer; die bearbeitende Person bekommt beim Lesen wieder den Platzhalter."""
    if "/notes#" not in (new_body or ""):
        return new_body
    old, new = _load_doc(old_body), _load_doc(new_body)
    if new is None:
        return new_body
    texts, in_blk = {}, {}
    if old is not None:
        def collect(s, blk):
            for m in _NOTE_LINK.finditer(s):
                texts.setdefault(int(m.group(1)), []).append(m.group(2))
                if blk:
                    in_blk.setdefault((blk, int(m.group(1))), []).append(m.group(2))
            return s
        _walk_strings(old, collect)
    seen, na = {}, _na_texts()
    changed = [False]

    def fn(s, blk):
        def rep(m):
            i = int(m.group(1))
            if i not in texts and m.group(2) in na:      # Platzhalter an neue Stelle kopiert
                tt = title_of(i) if title_of else None
                if tt is None:
                    return m.group(0)
                changed[0] = True
                return f'<a href="/notes#{i}">{tt}</a>'
            # Ziel inzwischen sichtbar (Freigabe nach dem Laden): der Platzhalter darf trotzdem nicht gespeichert werden
            if i not in texts or (visible(i) and m.group(2) not in na):
                return m.group(0)
            key = (blk, i) if (blk, i) in in_blk else i
            lst = in_blk[key] if key != i else texts[i]
            k = seen.get(key, 0); seen[key] = k + 1
            txt = lst[min(k, len(lst) - 1)]
            if txt == m.group(2):
                return m.group(0)
            changed[0] = True
            return f'<a href="/notes#{i}">{txt}</a>'
        return _NOTE_LINK.sub(rep, s) if "/notes#" in s else s
    out = _walk_strings(new, fn)
    return json.dumps(out, ensure_ascii=False) if changed[0] else new_body


def title_of(c, owner):
    """Fuer restore_links: Titel (bereinigt) einer Seite von owner oder None."""
    def f(i):
        r = c.execute("SELECT title FROM note WHERE id=? AND uid=?", (i, owner)).fetchone() if 0 < i < 2 ** 63 else None
        return _esc(r[0] or t("Unbenannt"), quote=False) if r else None
    return f


def edit_shared(c, owner, nid):
    """True, wenn Personen mit "Bearbeiten" diese Seite oder eine ihrer Unterseiten aendern koennen (Freigabe hier,
    darueber oder darunter) - fuer den Hinweis im Veroeffentlichen-Dialog."""
    if not enabled():
        return False
    edits = {r[0] for r in c.execute("SELECT note_id FROM note_share WHERE owner_uid=? AND role='edit'", (owner,))}
    if not edits:
        return False
    if edits & set(note_chain(c, owner, nid)):
        return True
    down = c.execute(
        "WITH RECURSIVE d(id, depth) AS (SELECT id, 0 FROM note WHERE parent_id=? AND uid=? AND deleted_at IS NULL"
        " UNION ALL SELECT n.id, d.depth + 1 FROM note n JOIN d ON n.parent_id=d.id WHERE n.uid=? AND n.deleted_at IS NULL"
        " AND d.depth < 64) SELECT id FROM d", (nid, owner, owner)).fetchall()
    return any(r[0] in edits for r in down)


def viewer(acc):
    """visible(id) fuer mask_links/restore_links: darf das Konto die Zielseite sehen (eigene oder freigegebene)?"""
    return lambda i: bool(acc.role(i))


def unread_notes(c, uid, owners):
    """Seiten (von den Eigentuemern owners) mit offenen Unterhaltungen, in denen etwas Neues von anderen steht -
    fuer den Punkt im Seitenbaum. Eine Abfrage fuer alle Seiten."""
    owners = [o for o in owners if o]
    if not owners:
        return set()
    return {r[0] for r in c.execute(
        f"SELECT DISTINCT r.note_id FROM note_comment r WHERE r.owner_uid IN ({','.join('?' * len(owners))}) AND r.thread_id IS NULL "
        "AND r.resolved_at IS NULL AND EXISTS (SELECT 1 FROM note_comment x WHERE (x.id=r.id OR x.thread_id=r.id) AND (x.uid IS NULL OR x.uid<>?) "
        "AND x.created > COALESCE((SELECT s.seen_at FROM note_comment_seen s WHERE s.uid=? AND s.thread_id=r.id), 0))", (*owners, uid, uid))}


def unread_count(c, uid):
    """Startseite: Anzahl Seiten (eigene und geteilte) mit neuen Kommentaren von anderen."""
    own = unread_notes(c, uid, [uid])
    if own:
        own = {r[0] for r in c.execute(f"SELECT id FROM note WHERE uid=? AND deleted_at IS NULL AND id IN ({','.join('?' * len(own))})",
                                       (uid, *own))}
    vis = visible_pages(c, uid)
    sh = unread_notes(c, uid, {o for o, _, _, _ in vis}) & {row[0] for _, _, row, _ in vis} if vis else set()
    return len(own) + len(sh)


# --- Konto/Gruppe geloescht ------------------------------------------------------------------------
def delete_account(c, uid):
    """Kontoloeschung: eigene Freigaben und Freigaben an das Konto weg, Kommentare des Kontos anonymisieren, Kommentare auf
    eigenen Seiten loeschen (die Seiten gehen mit). -> Anzahl entfernter Freigaben."""
    n = c.execute("DELETE FROM note_share WHERE owner_uid=? OR (target_type='user' AND target=?)", (uid, uid)).rowcount
    c.execute("DELETE FROM note_comment_seen WHERE uid=? OR thread_id IN (SELECT id FROM note_comment WHERE owner_uid=?)", (uid, uid))
    c.execute("DELETE FROM note_comment WHERE owner_uid=?", (uid,))
    c.execute("UPDATE note_comment SET uid=NULL WHERE uid=?", (uid,))
    c.execute("UPDATE note_comment SET resolved_by=NULL WHERE resolved_by=?", (uid,))
    # "zuletzt geaendert von" auf fremden Seiten: sonst zeigte ein spaeteres Konto mit gleichem Benutzernamen als Bearbeiter
    c.execute("UPDATE note SET updated_by=NULL WHERE updated_by=? AND uid<>?", (uid, uid))
    # ebenso "Angelegt von" und der Autor gesicherter Fassungen (Versionen) auf fremden Seiten
    c.execute("UPDATE note SET created_by=NULL WHERE created_by=? AND uid<>?", (uid, uid))
    c.execute("UPDATE note_version SET author=NULL WHERE author=? AND uid<>?", (uid, uid))
    return n


def drop_group(c, name):
    """Gruppe im Admin-Bereich geloescht: Freigaben an sie entfernen (ausser es gibt sie weiter aus LDAP/OIDC)."""
    if c.execute("SELECT 1 FROM user_ext_group WHERE name=? LIMIT 1", (name,)).fetchone():
        return 0
    # Foederation: was andere Instanzen mit der Gruppe geteilt haben, endet hier ebenso - sonst bestaetigte dieser Server
    # die Mitgliedschaft einer spaeter gleichnamig neu angelegten Gruppe (andere Personen) weiter signiert
    c.execute("DELETE FROM fed_inbound WHERE target_type='group' AND target=?", (name,))
    return c.execute("DELETE FROM note_share WHERE target_type='group' AND target=?", (name,)).rowcount


def share_counts(c, uid):
    """Admin, Benutzer-Detail: nur Anzahlen."""
    return (c.execute("SELECT COUNT(*) FROM note_share WHERE owner_uid=?", (uid,)).fetchone()[0],
            c.execute("SELECT COUNT(*) FROM note_share WHERE target_type='user' AND target=?", (uid,)).fetchone()[0])


# --- Endpunkte -------------------------------------------------------------------------------------
@router.get("/api/shared")
def api_shared(request: Request):
    """Mit mir geteilt: alle fremden Seiten, die ich sehen darf (flach; root=true = oberste geteilte Seite)."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    return JSONResponse(shared_list(uid), headers={"Cache-Control": "no-store"})


def shared_list(uid):
    """Fuer die Weboberflaeche und die Foederation (uid = fremde Person '@name@host')."""
    with _db() as c:
        vis = visible_pages(c, uid)
        nm = names(c, {o for o, _, _, _ in vis})
        un = unread_notes(c, uid, {o for o, _, _, _ in vis})
    out = []
    for o, role, row, root in vis:
        d = _note_meta(row)
        d.update(role=role, root=root, shared=True, pinned=False, owner={"name": nm.get(o) or o, "username": ref_of(None, o, uid), "me": False,
                                                                  "avatar": nm.avatar(o)},
                 unread=row[0] in un)
        if root:
            d["parent_id"] = None                     # oberste geteilte Seite: Eltern sind fuer mich nicht sichtbar
        out.append(d)
    return out


@router.get("/api/share/info")
def api_share_info(request: Request):
    """Fuer Empfaenger: ist Teilen an, und wie viele Freigaben gibt es an mich (direkt oder ueber meine Gruppen)?
    Ist Teilen aus, zeigt die Seitenleiste damit einen Hinweis statt still nichts."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        acc = Access(c, uid)
        sql, args = acc._principals()
        n = c.execute(f"SELECT COUNT(*) FROM note_share WHERE owner_uid<>? AND {sql}", (uid, *args)).fetchone()[0]
    return JSONResponse({"enabled": enabled(), "received": n}, headers={"Cache-Control": "no-store"})


def _state(c, uid, nid):
    rows = c.execute("SELECT target_type, target, role, created FROM note_share WHERE note_id=? AND owner_uid=? ORDER BY created, id",
                     (nid, uid)).fetchall()
    chain = note_chain(c, uid, nid)
    inh = []
    if len(chain) > 1:
        up = chain[1:]
        inh = c.execute(f"SELECT s.note_id, n.title, s.target_type, s.target, s.role FROM note_share s JOIN note n ON n.id=s.note_id "
                        f"WHERE s.owner_uid=? AND s.note_id IN ({','.join('?' * len(up))})", (uid, *up)).fetchall()
        order = {x: i for i, x in enumerate(up)}
        inh.sort(key=lambda r: order.get(r[0], 99))
    nm = names(c, [r[1] for r in rows if r[0] in ("user", "remote")] + [r[3] for r in inh if r[2] in ("user", "remote")])
    who = lambda typ, tg: nm.get(tg, tg) if typ in ("user", "remote") else tg
    titles = {r[0]: r[1] or t("Unbenannt") for r in inh}

    def via(user):
        """Weitere Wege, auf denen user diese Seite sieht: Gruppen-Freigaben hier oder darueber, eigene Freigaben darueber."""
        gs, out = None, []
        for typ, tg, role, src in [(r[0], r[1], r[2], nid) for r in rows] + [(r[2], r[3], r[4], r[0]) for r in inh]:
            if typ in REMOTE_TYPES:
                continue
            if typ == "user" and (tg != user or src == nid):
                continue
            if typ == "group":
                gs = groups_of(c, user) if gs is None else gs
                if tg not in gs:
                    continue
            out.append({"type": typ, "name": who(typ, tg), "role": role, "note_id": src, "title": titles.get(src)})
        out.sort(key=lambda x: -RANK[x["role"]])
        return out

    def members(g):
        return c.execute("SELECT COUNT(DISTINCT u.id) FROM users u WHERE u.disabled=0 AND (EXISTS (SELECT 1 FROM user_group ug JOIN groups gr "
                         "ON gr.id=ug.group_id WHERE ug.user_id=u.id AND gr.name=?) OR EXISTS (SELECT 1 FROM user_ext_group e "
                         "WHERE e.user_id=u.id AND e.name=?))", (g, g)).fetchone()[0]

    shares = []
    for r in rows:
        d = {"type": r[0], "target": ref_of(c, r[1], uid) if r[0] == "user" else r[1], "name": who(r[0], r[1]), "role": r[2], "created": r[3]}
        if r[0] in ("user", "remote"):
            d["avatar"] = nm.avatar(r[1])
        if r[0] == "user":       # tatsaechliche Rolle: hoechste aus direkter Freigabe, Gruppen und Seiten darueber
            v = via(r[1])
            d.update(via=v, effective=_best(r[2], v[0]["role"] if v else None))
        elif r[0] == "group":
            d.update(members=members(r[1]), effective=r[2])
        else:                    # andere Instanz: Rolle wie eingetragen (Gruppen dort kennt nur die andere Instanz)
            d.update(effective=r[2], remote=True)
        shares.append(d)
    return {"enabled": enabled(), "scope": group_scope(), "max": SHARE_MAX, "shareable": bool(chain),
            "federation": fed_enabled() and REMOTE_TARGET is not None and fed_share_ok(),
            "shares": shares,
            "inherited": [{"note_id": r[0], "title": r[1] or t("Unbenannt"), "type": r[2], "target": ref_of(c, r[3], uid) if r[2] == "user" else r[3],
                           "name": who(r[2], r[3]), "role": r[4]}
                          for r in inh],
            "groups": sorted(groups_of(c, uid))}


def _own(c, uid, nid):
    """Freigaben verwalten darf nur der Eigentuemer -> fehlerantwort oder None."""
    _, _, err = access(c, uid, nid, "owner")
    if err is not None:
        return err
    if not c.execute("SELECT 1 FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, uid)).fetchone():
        return _bad("not found", 404)
    return None


@router.get("/api/notes/{nid}/shares")
def api_shares_get(nid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        err = _own(c, uid, nid)
        if err is not None:
            return err
        return JSONResponse(_state(c, uid, nid), headers={"Cache-Control": "no-store"})


def _target(b, c=None):
    typ = b.get("type") if b.get("type") in ("user", "group", *REMOTE_TYPES) else None
    tg = str(b.get("target") or "").strip()[:300]
    if typ == "user" and c is not None:
        return typ, resolve_ref(c, tg)            # 2.4: Handle ('~…') oder wie bisher der Benutzername
    return typ, tg.lower()


@router.put("/api/notes/{nid}/shares")
async def api_shares_put(nid: int, request: Request):
    """Freigabe anlegen oder Rolle aendern: {type: 'user'|'group', target, role: 'view'|'comment'|'edit'}."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request)
    if b is None:
        return _bad(t("ungültige Anfrage"))
    typ, tg = _target(b)
    if typ == "user" and tg:
        with _db() as c0:
            tg = resolve_ref(c0, tg)                 # 2.4: Handle ('~…') oder Benutzername
    role = b.get("role")
    if typ in REMOTE_TYPES:
        # andere Instanz: erst Eigentum pruefen, dann Verbindung und Person/Gruppe dort nachfragen (Netzwerk -
        # ausserhalb der Transaktion), danach wie gewohnt speichern
        with _db() as c:
            err = _own(c, uid, nid)
        if err is not None:
            return err
        if not enabled():
            return _bad(t("Teilen ist auf diesem Server abgeschaltet."), 403)
        if role not in ROLES or not tg:
            return _bad(t("ungültige Angabe"))
        if REMOTE_TARGET is None or not fed_enabled() or not fed_share_ok(tg):
            return _bad(t("Teilen mit anderen Saga-Instanzen ist auf diesem Server nicht eingeschaltet."), 403)
        tg, msg, code = await run_in_threadpool(REMOTE_TARGET, uid, typ, tg, nid)
        if msg:
            return _bad(msg, code)
    with _db() as c, c:
        err = _own(c, uid, nid)
        if err is not None:
            return err
        if not enabled():
            return _bad(t("Teilen ist auf diesem Server abgeschaltet."), 403)
        if not typ or not tg or role not in ROLES:
            return _bad(t("ungültige Angabe"))
        if not note_chain(c, uid, nid):
            return _bad(t("Archivierte Seiten und Vorlagen lassen sich nicht teilen."))
        mine = groups_of(c, uid)
        if typ in REMOTE_TYPES:
            pass                                    # oben geprueft (Verbindung aktiv, Person/Gruppe dort vorhanden)
        elif typ == "user":
            u = c.execute("SELECT disabled FROM users WHERE username=?", (tg,)).fetchone()
            if tg == uid:
                return _bad(t("Mit dir selbst musst du nicht teilen."))
            if not u or u[0]:
                return _bad(t("Dieses Konto gibt es nicht."))
            if group_scope() and not (mine & groups_of(c, tg)):
                return _bad(t("Teilen ist nur mit Personen aus deinen Gruppen erlaubt."), 403)
        else:
            tg = auth.norm_group(tg) or ""
            if tg not in mine:
                return _bad(t("Teilen geht nur mit Gruppen, in denen du selbst bist."))
        exists = c.execute("SELECT role FROM note_share WHERE note_id=? AND target_type=? AND target=?", (nid, typ, tg)).fetchone()
        if not exists and c.execute("SELECT COUNT(*) FROM note_share WHERE note_id=?", (nid,)).fetchone()[0] >= SHARE_MAX:
            return _bad(t("Höchstens {n} Freigaben je Seite.", n=SHARE_MAX), 409)
        if exists:
            c.execute("UPDATE note_share SET role=? WHERE note_id=? AND target_type=? AND target=?", (role, nid, typ, tg))
        else:
            c.execute("INSERT INTO note_share (note_id, owner_uid, target_type, target, role, created, created_by) VALUES (?,?,?,?,?,?,?)",
                      (nid, uid, typ, tg, role, time.time(), uid))
        # Benachrichtigung (saga.notify) + "Mit mir geteilt" live neu laden (saga.live) - nach dem Commit zugestellt
        bus.fire("share.set", c, owner=uid, note_id=nid, target_type=typ, target=tg, role=role,
                 old_role=exists[0] if exists else None, actor=uid)
        return JSONResponse(_state(c, uid, nid), headers={"Cache-Control": "no-store"})


@router.delete("/api/notes/{nid}/shares")
async def api_shares_delete(nid: int, request: Request):
    """Freigabe entfernen: ?type=user|group&target=... (oder dasselbe als JSON)."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    q = dict(request.query_params)
    if not q.get("type"):
        q = await _body(request) or {}
    typ, tg = _target(q)
    with _db() as c, c:
        if typ == "user" and tg:
            tg = resolve_ref(c, tg)
        err = _own(c, uid, nid)
        if err is not None:
            return err
        if not typ or not tg:
            return _bad(t("ungültige Angabe"))
        n = c.execute("DELETE FROM note_share WHERE note_id=? AND owner_uid=? AND target_type=? AND target=?", (nid, uid, typ, tg)).rowcount
        if n:
            bus.fire("share.removed", c, owner=uid, note_id=nid, target_type=typ, target=tg)
        return JSONResponse(dict(_state(c, uid, nid), removed=bool(n)), headers={"Cache-Control": "no-store"})


_tgt_hits = {}       # uid -> [zeitstempel] (Personensuche: hoechstens 60 Anfragen je Minute)


def _like(q):
    return re.sub(r"([\\%_])", r"\\\1", q)


SHORT_Q = 3          # Personensuche: ab so vielen Zeichen Teilwort-Suche, darunter nur genaue Treffer (kurze Namen wie "hd")


def name_match():
    """SQL-Bedingung der lokalen Personensuche (Tabelle users u) - passend zu name_args(q)."""
    return "(u.username LIKE ? OR lower(u.display_name) LIKE ?)"


def name_args(q):
    """Ab SHORT_Q Zeichen: Benutzername beginnt mit q oder Anzeigename enthaelt q; bei 1-2 Zeichen nur genaue Treffer
    (Gross/Klein egal) - so laesst sich mit kurzen Anfragen keine Liste aller Konten abrufen."""
    if len(q) < SHORT_Q:
        return [_like(q), _like(q)]
    pat = _like(q)
    return [pat + "%", "%" + pat + "%"]


@router.get("/api/share/targets")
def api_share_targets(request: Request, q: str = ""):
    """Personensuche fuer den Teilen-Dialog: hoechstens 10 Konten (Anzeigename + Benutzername; ab 3 Zeichen Teilwort-Suche,
    bei 1-2 Zeichen nur genaue Treffer), nie die ganze Liste; mit "nur gemeinsame Gruppen" nur Konten aus eigenen Gruppen. Dazu die eigenen Gruppen, die passen."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    q = re.sub(r"\s+", " ", q or "").strip().lower()[:60]
    now = time.time()
    hits = [x for x in _tgt_hits.get(uid, []) if now - x < 60]
    if len(hits) >= 60:
        return _bad(t("zu schnell"), 429)
    _tgt_hits[uid] = hits + [now]
    if len(_tgt_hits) > 5000:
        _tgt_hits.clear()
    if not q or not enabled():
        return JSONResponse({"users": [], "groups": []}, headers={"Cache-Control": "no-store"})
    with _db() as c:
        mine = sorted(groups_of(c, uid))
        pat = _like(q)
        sql = "SELECT u.username, u.display_name FROM users u WHERE u.disabled=0 AND u.username<>? AND " + name_match()
        args = [uid] + name_args(q)
        if group_scope():
            if not mine:
                rows = []
                sql = None
            else:
                ph = ",".join("?" * len(mine))
                sql += (f" AND (EXISTS (SELECT 1 FROM user_group ug JOIN groups g ON g.id=ug.group_id WHERE ug.user_id=u.id AND g.name IN ({ph}))"
                        f" OR EXISTS (SELECT 1 FROM user_ext_group e WHERE e.user_id=u.id AND e.name IN ({ph})))")
                args += mine + mine
        if sql:
            rows = c.execute(sql + " ORDER BY (u.username=?) DESC, (u.username LIKE ?) DESC, u.username LIMIT ?",
                             (*args, q, pat + "%", TARGETS_MAX)).fetchall()
        av = avatars.urls(c, [r[0] for r in rows]) if rows else {}
        lb = labels(c, [r[0] for r in rows]) if rows else {}
    # 2.4 (W4): Kennung = pseudonymer Handle, Name ohne Anmeldename (PUT /shares nimmt den Handle an)
    users = [{"username": ref_of(None, r[0], uid), "name": lb.get(r[0]) or ref_of(None, r[0], uid), "avatar": av.get(r[0])} for r in rows]
    groups = [{"name": g} for g in mine if (q in g if len(q) >= SHORT_Q else q == g)][:TARGETS_MAX]
    out = {"users": users, "groups": groups}
    # name@host einer verbundenen Instanz: dort nachfragen (signiert) - nur genaue Adressen, keine Suche ueber Instanzen
    if REMOTE_SEARCH is not None and "@" in q.lstrip("@") and fed_enabled() and fed_share_ok(q):
        out["remote"], hint = REMOTE_SEARCH(uid, q)
        if hint and not users and not groups:
            out["remote_hint"] = hint                      # warum nichts gefunden wurde (z. B. E-Mail statt oeffentlichem Namen)
    if DIR_SEARCH is not None and fed_enabled():           # 2.3: Adressbuecher verbundener Instanzen (nach Anzeigename)
        seen = {h["target"] for h in out.get("remote") or []}
        extra = [h for h in DIR_SEARCH(uid, q, "share") if h["target"] not in seen]
        if extra:
            out["remote"] = extra + (out.get("remote") or [])
            out.pop("remote_hint", None)
    return JSONResponse(out, headers={"Cache-Control": "no-store"})
