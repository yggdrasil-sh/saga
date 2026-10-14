"""Teilen (1.3): Freigaben einer Seite an Konten und Gruppen, die zentrale Zugriffspruefung und die Personensuche.

Rollen: owner (Eigentuemer) > edit (Bearbeiten) > comment (Kommentieren) > view (Lesen) > keine.
- Eine Freigabe gilt fuer die Seite und alle Unterseiten/Eintraege (Vererbung ueber parent_id); es zaehlt die hoechste
  Rolle in der Kette. Die Kette endet vor geloeschten/archivierten Seiten und Vorlagen (notes.note_chain, wie beim
  Veroeffentlichen) - solche Seiten sieht nur der Eigentuemer.
- Gruppen: lokale (user_group) und aus LDAP/OIDC (user_ext_group), immer frisch aus der Datenbank gelesen -
  Gruppenwechsel und Entzug wirken mit der naechsten Anfrage.
- Endpunkte antworten ohne Rolle mit 404 (die Seite "gibt es nicht"), mit zu kleiner Rolle mit 403.
- Fuer Listen (Seitenleiste "Mit mir geteilt", Suche, Export) gibt es Access.tree(): eine Abfrage je Eigentuemer statt
  einer Kette je Seite."""
import json
import re
import time
from html import escape as _esc
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
import auth
from db import connect as _db
from saga import bus, i18n
from saga.core import _api_guard, _bad, _body, t
from saga.notes import NOTE_META_COLS, _note_meta, note_chain

router = APIRouter()

ROLES = ("view", "comment", "edit")
RANK = {None: 0, "view": 1, "comment": 2, "edit": 3, "owner": 4}
SHARE_MAX = 200                                  # Freigaben je Seite
TARGETS_MAX = 10                                 # Treffer der Personensuche


def enabled():
    return bool(auth.settings().get("share_enabled", True))


def group_scope():
    return bool(auth.settings().get("share_group_scope", False))


def _best(a, b):
    return a if RANK.get(a, 0) >= RANK.get(b, 0) else b


def groups_of(c, uid):
    """Gruppen eines Kontos (lokal + LDAP/OIDC), frisch aus der Datenbank."""
    return {r[0] for r in c.execute(
        "SELECT g.name FROM user_group ug JOIN groups g ON g.id=ug.group_id JOIN users u ON u.id=ug.user_id WHERE u.username=? "
        "UNION SELECT e.name FROM user_ext_group e JOIN users u ON u.id=e.user_id WHERE u.username=?", (uid, uid))}


def names(c, uids):
    """Benutzername -> Anzeigename (sonst Benutzername). Unbekannte/geloeschte fehlen."""
    uids = [u for u in set(uids) if u]
    if not uids:
        return {}
    return {r[0]: (r[1] or r[0]) for r in c.execute(
        f"SELECT username, display_name FROM users WHERE username IN ({','.join('?' * len(uids))})", tuple(uids))}


def person(c, uid, me, cache=None):
    """{name, username, me} fuer Eigentuemer/Verfasser; uid None/geloescht -> 'Geloeschtes Konto'."""
    nm = (cache if cache is not None else names(c, [uid])).get(uid) if uid else None
    if not nm:
        return {"name": t("Gelöschtes Konto"), "username": None, "me": False}
    return {"name": nm, "username": uid, "me": uid == me}


class Access:
    """Zugriff EINES Kontos auf Seiten - je Anfrage einmal anlegen; Eigentuemer, Freigaben, Gruppen und Rollen werden
    zwischengespeichert (kurzer Cache je Anfrage)."""

    def __init__(self, c, uid):
        self.c, self.uid = c, uid
        self._on, self._groups = None, None
        self._owner, self._role, self._shares, self._tree = {}, {}, {}, {}

    def on(self):
        if self._on is None:
            self._on = enabled()
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
        sql = "((target_type='user' AND target=?)" + (f" OR (target_type='group' AND target IN ({','.join('?' * len(g))}))" if g else "") + ")"
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
        """Alle Seiten von owner, die ich sehen darf: {id: rolle} (meta=True: {id: (rolle, zeile)}) - EINE Abfrage fuer den
        ganzen Baum, Vererbung in Python (Seitenleiste mit 500 Seiten = eine Abfrage, nicht 500 Ketten)."""
        if owner == self.uid or not self.on():
            return {}
        sh = self.shares_from(owner)
        if not sh:
            self._tree[owner] = {}
            return {}
        cols = f", {NOTE_META_COLS}" if meta else ""
        rows = self.c.execute(f"SELECT n.id, n.parent_id, (n.deleted_at IS NOT NULL OR n.archived IS NOT NULL OR n.is_template<>0){cols} "
                              "FROM note n WHERE n.uid=? ORDER BY n.sort NULLS FIRST, n.created", (owner,)).fetchall()
        par = {r[0]: r[1] for r in rows}
        bad = {r[0]: bool(r[2]) for r in rows}
        memo = {}
        for n in par:
            stack, x = [], n
            while x is not None and x in par and x not in memo and len(stack) < 80:
                stack.append(x)
                x = par[x]
            up = memo.get(x)
            for y in reversed(stack):
                up = None if bad[y] else _best(sh.get(y), up)
                memo[y] = up
        roles = {k: v for k, v in memo.items() if v}
        self._tree[owner] = roles
        for k, v in roles.items():
            self._role.setdefault(k, v)
        if meta:
            return {r[0]: (roles[r[0]], r[3:]) for r in rows if r[0] in roles}
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
    with _db() as c:
        vis = visible_pages(c, uid)
        nm = names(c, {o for o, _, _, _ in vis})
        un = unread_notes(c, uid, {o for o, _, _, _ in vis})
    out = []
    for o, role, row, root in vis:
        d = _note_meta(row)
        d.update(role=role, root=root, shared=True, pinned=False, owner={"name": nm.get(o) or o, "username": o, "me": False},
                 unread=row[0] in un)
        if root:
            d["parent_id"] = None                     # oberste geteilte Seite: Eltern sind fuer mich nicht sichtbar
        out.append(d)
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


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
    nm = names(c, [r[1] for r in rows if r[0] == "user"] + [r[3] for r in inh if r[2] == "user"])
    who = lambda typ, tg: nm.get(tg, tg) if typ == "user" else tg
    titles = {r[0]: r[1] or t("Unbenannt") for r in inh}

    def via(user):
        """Weitere Wege, auf denen user diese Seite sieht: Gruppen-Freigaben hier oder darueber, eigene Freigaben darueber."""
        gs, out = None, []
        for typ, tg, role, src in [(r[0], r[1], r[2], nid) for r in rows] + [(r[2], r[3], r[4], r[0]) for r in inh]:
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
        d = {"type": r[0], "target": r[1], "name": who(r[0], r[1]), "role": r[2], "created": r[3]}
        if r[0] == "user":       # tatsaechliche Rolle: hoechste aus direkter Freigabe, Gruppen und Seiten darueber
            v = via(r[1])
            d.update(via=v, effective=_best(r[2], v[0]["role"] if v else None))
        else:
            d.update(members=members(r[1]), effective=r[2])
        shares.append(d)
    return {"enabled": enabled(), "scope": group_scope(), "max": SHARE_MAX, "shareable": bool(chain),
            "shares": shares,
            "inherited": [{"note_id": r[0], "title": r[1] or t("Unbenannt"), "type": r[2], "target": r[3], "name": who(r[2], r[3]), "role": r[4]}
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


def _target(b):
    typ = b.get("type") if b.get("type") in ("user", "group") else None
    tg = str(b.get("target") or "").strip().lower()[:100]
    return typ, tg


@router.put("/api/notes/{nid}/shares")
async def api_shares_put(nid: int, request: Request):
    """Freigabe anlegen oder Rolle aendern: {type: 'user'|'group', target, role: 'view'|'comment'|'edit'}."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request)
    if b is None:
        return _bad(t("ungueltige Anfrage"))
    typ, tg = _target(b)
    role = b.get("role")
    with _db() as c, c:
        err = _own(c, uid, nid)
        if err is not None:
            return err
        if not enabled():
            return _bad(t("Teilen ist auf diesem Server abgeschaltet."), 403)
        if not typ or not tg or role not in ROLES:
            return _bad(t("ungueltige Angabe"))
        if not note_chain(c, uid, nid):
            return _bad(t("Archivierte Seiten und Vorlagen lassen sich nicht teilen."))
        mine = groups_of(c, uid)
        if typ == "user":
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
        err = _own(c, uid, nid)
        if err is not None:
            return err
        if not typ or not tg:
            return _bad(t("ungueltige Angabe"))
        n = c.execute("DELETE FROM note_share WHERE note_id=? AND owner_uid=? AND target_type=? AND target=?", (nid, uid, typ, tg)).rowcount
        if n:
            bus.fire("share.removed", c, owner=uid, note_id=nid, target_type=typ, target=tg)
        return JSONResponse(dict(_state(c, uid, nid), removed=bool(n)), headers={"Cache-Control": "no-store"})


_tgt_hits = {}       # uid -> [zeitstempel] (Personensuche: hoechstens 60 Anfragen je Minute)


def _like(q):
    return re.sub(r"([\\%_])", r"\\\1", q)


@router.get("/api/share/targets")
def api_share_targets(request: Request, q: str = ""):
    """Personensuche fuer den Teilen-Dialog: ab 3 Zeichen, hoechstens 10 Konten (Anzeigename + Benutzername), nie die
    ganze Liste; mit "nur gemeinsame Gruppen" nur Konten aus eigenen Gruppen. Dazu die eigenen Gruppen, die passen."""
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
    if len(q) < 3 or not enabled():
        return JSONResponse({"users": [], "groups": []}, headers={"Cache-Control": "no-store"})
    with _db() as c:
        mine = sorted(groups_of(c, uid))
        pat = _like(q)
        sql = ("SELECT u.username, u.display_name FROM users u WHERE u.disabled=0 AND u.username<>? "
               "AND (u.username LIKE ? OR lower(u.display_name) LIKE ?)")
        args = [uid, pat + "%", "%" + pat + "%"]
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
    users = [{"username": r[0], "name": r[1] or r[0]} for r in rows]
    groups = [{"name": g} for g in mine if q in g][:TARGETS_MAX]
    return JSONResponse({"users": users, "groups": groups}, headers={"Cache-Control": "no-store"})
