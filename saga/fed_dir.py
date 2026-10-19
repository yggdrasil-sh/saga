"""Adressbuch-Abgleich zwischen verbundenen Saga-Instanzen (2.3, Protokoll 2, Faehigkeit 'directory').

Verbundene Instanzen gleichen ihre Adressbuecher ab: je Person Anzeigename, Profilbild-Stand, Server und der stabile,
pseudonyme Handle ('~' + 16 Zeichen, saga.federation.own_handle) - dazu der oeffentliche Name, falls die Person einen hat
(damit laesst sich wie bisher teilen). Nie: Anmeldename, E-Mail, Gruppen-Mitgliedschaften.

- Eigene Seite: fed_dir_local haelt den Stand je Konto mit fortlaufender Nummer (seq). refresh_local() vergleicht ihn mit
  den Konten (hoechstens alle REFRESH_MIN Sekunden; Aenderungen -> neue Nummer, verschwundene Konten -> Grabstein) und
  meldet verbundenen Instanzen 'directory.changed' (Express, sonst Ausgangskorb, Kette 'dir').
- GET /fed/v2/directory?since=N&cfg=X (signiert): Eintraege mit seq > N, fuer die anfragende Instanz gefiltert (Schalter je
  Konto und je Instanz, deaktivierte Konten nie, "nur gemeinsame Gruppen" nur mit Admin-Ausnahme, Gruppen-Auswahl je
  Instanz). Passt cfg (Filter-Stand) nicht mehr oder ist N aelter als der aelteste Grabstein: alles neu (full).
- Andere Seite: fed_directory je Instanz; holt bei 'directory.changed', nach dem Verbinden und alle SYNC_EVERY Sekunden.
  Trennen oder Abschalten loescht das fremde Adressbuch sofort.
- Suche (Chat, Teilen; spaeter Erwaehnungen): nach Anzeigename, Treffer mit Herkunft ("Anna Berg · b.example")."""
import hashlib
import json
import re
import threading
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from db import connect as _db
from saga import avatars, federation as F, fedsig, sharing
from saga.core import _api_guard, _bad, _body, t

router = APIRouter()

PAGE_MAX = 1000                  # Eintraege je Antwort
SYNC_EVERY = 6 * 3600            # fremde Adressbuecher spaetestens so oft abgleichen
REFRESH_MIN = 5.0                # eigenes Adressbuch hoechstens so oft neu berechnen
REFRESH_EVERY = 60.0             # ... im Hintergrund (Namen, Bilder, Gruppen aendern sich auch ohne Ereignis)
TOMB_KEEP = 30 * 86400           # Grabsteine so lange (danach: wer aelter fragt, bekommt alles neu)
SEARCH_MAX = 10
KV = "fed_directory"             # user_kv: '0' = Konto erscheint nicht in Adressbuechern verbundener Server
_st = {"refresh": 0.0, "bg": 0.0, "sync": 0.0}


# =====================================================================================================
# Eigenes Adressbuch
# =====================================================================================================
def account_on(c, uid):
    r = c.execute("SELECT v FROM user_kv WHERE uid=? AND k=?", (uid, KV)).fetchone()
    return not r or r[0] != "0"


def _groups_all(c):
    out = {}
    for u, g in c.execute("SELECT u.username, g.name FROM user_group ug JOIN groups g ON g.id=ug.group_id JOIN users u ON u.id=ug.user_id "
                          "UNION SELECT u.username, e.name FROM user_ext_group e JOIN users u ON u.id=e.user_id").fetchall():
        out.setdefault(u, set()).add(g)
    return out


def _current(c):
    """uid -> (name, pub, av, groups_json) fuer alle Konten, die im Adressbuch stehen duerfen (aktiv, Schalter an, mit Namen)."""
    rows = c.execute("SELECT u.username, u.display_name, p.name, kv.v, u.email FROM users u LEFT JOIN public_name p ON p.uid=u.username "
                     "LEFT JOIN user_kv kv ON kv.uid=u.username AND kv.k=? WHERE u.disabled=0", (KV,)).fetchall()
    uids = [r[0] for r in rows if r[3] != "0"]
    av = avatars.urls(c, uids, known_only=True) if uids else {}
    gs = _groups_all(c)
    out = {}
    for uid, disp, pub, flag, email in rows:
        if flag == "0":
            continue
        name = F.safe_display(disp, uid, email) or (pub or "")          # nie der Anmeldename, nie die E-Mail-Adresse
        if not name:
            continue                                                     # ohne Namen nicht auffindbar
        a = av.get(uid)
        out[uid] = (name, pub or "", hashlib.sha256(a.encode()).hexdigest()[:16] if a else "", json.dumps(sorted(gs.get(uid, ()))))
    return out


def refresh_local(force=False):
    """Stand der eigenen Konten mit fed_dir_local abgleichen. -> True, wenn sich etwas geaendert hat."""
    now = time.time()
    if not force and now - _st["refresh"] < REFRESH_MIN:
        return False
    _st["refresh"] = now
    changed = False
    with _db() as c, c:
        c.execute("SELECT pg_advisory_xact_lock(424243)")              # mehrere Prozesse: einer nach dem anderen
        cur = _current(c)
        old = {r[0]: r[1:] for r in c.execute("SELECT uid, handle, name, pub, av, groups, visible FROM fed_dir_local").fetchall()}
        for uid, (name, pub, av, gj) in cur.items():
            o = old.get(uid)
            if o and o[5] and (o[1], o[2], o[3], o[4]) == (name, pub, av, gj):
                continue
            h = F.own_handle(c, uid)
            c.execute("INSERT INTO fed_dir_local (uid, handle, name, pub, av, groups, visible, seq, updated) "
                      "VALUES (?,?,?,?,?,?,1,nextval('fed_dir_seq'),?) ON CONFLICT (uid) DO UPDATE SET handle=EXCLUDED.handle, "
                      "name=EXCLUDED.name, pub=EXCLUDED.pub, av=EXCLUDED.av, groups=EXCLUDED.groups, visible=1, seq=EXCLUDED.seq, "
                      "updated=EXCLUDED.updated", (uid, h, name, pub, av, gj, now))
            changed = True
        for uid, o in old.items():
            if o[5] and uid not in cur:                                 # abgeschaltet, deaktiviert, geloescht, ohne Namen
                c.execute("UPDATE fed_dir_local SET visible=0, name='', pub='', av='', groups='[]', seq=nextval('fed_dir_seq'), updated=? "
                          "WHERE uid=?", (now, uid))
                changed = True
        gone = c.execute("SELECT max(seq) FROM fed_dir_local WHERE visible=0 AND updated<?", (now - TOMB_KEEP,)).fetchone()[0]
        if gone:
            c.execute("DELETE FROM fed_dir_local WHERE visible=0 AND updated<?", (now - TOMB_KEEP,))
            c.execute("INSERT INTO setting (k, v) VALUES ('fed_dir_floor', ?) ON CONFLICT (k) DO UPDATE SET v=EXCLUDED.v", (str(gone),))
        if changed:
            _announce(c)
    return changed


def _announce(c, only=None, full=False):
    """'directory.changed' an verbundene Instanzen (Kette 'dir'); nur eines je Instanz wartet gleichzeitig."""
    for p in F.peers(c):
        if only is not None and p["id"] != only:
            continue
        if not F.feature(p, "directory"):
            continue
        if not full and c.execute("SELECT 1 FROM fed_outbox WHERE peer_id=? AND chain='dir' AND locked_until=0 LIMIT 1", (p["id"],)).fetchone():
            continue
        F._enqueue(c, p["id"], {"type": "directory.changed", **({"full": True} if full else {})}, chain="dir")


def _cfg(p):
    """Filter-Stand fuer eine Instanz: aendert er sich, holt sie alles neu."""
    o = p["opts"]
    raw = json.dumps([sharing.group_scope(), o.get("scope_exception"), o.get("directory_groups"), o.get("directory")])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _allowed(p):
    """-> (darf ueberhaupt, erlaubte Gruppen oder None = alle)"""
    if not F.feature(p, "directory"):
        return False, None
    o = p["opts"]
    if sharing.group_scope() and not o.get("scope_exception"):
        return False, None                   # "nur gemeinsame Gruppen": ohne Ausnahme kein Adressbuch fuer andere Instanzen
    return True, set(o.get("directory_groups") or []) or None


def dir_visible(c, uid, p):
    """Steht das Konto im Adressbuch fuer Instanz p? (z. B. Profilbild ueber den pseudonymen Handle)"""
    ok, groups = _allowed(p)
    if not ok:
        return False
    r = c.execute("SELECT groups FROM fed_dir_local WHERE uid=? AND visible=1", (uid,)).fetchone()
    return bool(r) and (groups is None or bool(groups & set(json.loads(r[0]))))


F.DIR_VISIBLE = dir_visible


def export(p, since, cfg):
    """Antwort fuer GET /fed/v2/directory."""
    refresh_local()
    ok, groups = _allowed(p)
    mine = _cfg(p)
    with _db() as c:
        floor = c.execute("SELECT v FROM setting WHERE k='fed_dir_floor'").fetchone()
        full = since <= 0 or cfg != mine or (floor is not None and since < int(floor[0]))
        if not ok:
            return {"full": True, "entries": [], "next": 0, "more": False, "cfg": mine, "enabled": False}
        start = 0 if full else since
        rows = c.execute("SELECT handle, name, pub, av, groups, visible, seq FROM fed_dir_local WHERE seq>? ORDER BY seq LIMIT ?",
                         (start, PAGE_MAX + 1)).fetchall()
    more = len(rows) > PAGE_MAX
    rows = rows[:PAGE_MAX]
    out = []
    for h, name, pub, av, gj, vis, _ in rows:
        show = vis and (groups is None or bool(groups & set(json.loads(gj))))
        if show:
            e = {"handle": h, "name": name}
            if pub:
                e["pub"] = pub
            if av:
                e["av"] = av
            out.append(e)
        elif not full:
            out.append({"handle": h, "deleted": True})
    return {"full": full, "entries": out, "next": rows[-1][6] if rows else (0 if full else since), "more": more, "cfg": mine, "enabled": True}


@router.get("/fed/v2/directory")
async def fed_directory(request: Request, since: int = 0, cfg: str = ""):
    inp, err = await F._verify(request)
    if err is not None:
        return err
    if not F.feature(inp.peer, "directory"):
        return F._signed(inp, 403, {"error": "directory disabled"})
    d = await run_in_threadpool(export, inp.peer, max(0, min(since, 2 ** 62)), str(cfg or "")[:40])
    return F._signed(inp, 200, d)


# =====================================================================================================
# Fremde Adressbuecher holen
# =====================================================================================================
_H = re.compile(r"~[a-z2-7]{16}")
_AV = re.compile(r"[0-9a-f]{1,32}")


def _forget(c, host, pairs):
    """Eintraege (handle, pub) sind aus dem Adressbuch einer Instanz verschwunden: Anzeigenamen nur noch, wo die Personen
    hier vorkommen (saga.federation.prune_persons)."""
    prs = []
    for h, pub in pairs:
        prs.append(f"@{h}@{host}")
        if pub:
            prs.append(f"@{pub}@{host}")
    if prs:
        F.prune_persons(c, principals=prs)


def _drop(c, pid):
    p = F.peer_by(c, "id", pid)
    gone = c.execute("DELETE FROM fed_directory WHERE peer_id=? RETURNING handle, pub", (pid,)).fetchall()
    c.execute("UPDATE fed_peer SET dir_seq=0, dir_cfg='', dir_at=? WHERE id=?", (time.time(), pid))
    if p:
        _forget(c, p["host"], gone)


def _principal(e, host):
    return f"@{e['pub'] or e['handle']}@{host}"


def pull(pid, full=False):
    """Adressbuch einer Instanz abgleichen (Netzwerk). -> Anzahl Aenderungen oder None (nicht moeglich/Fehler)."""
    with _db() as c:
        p = F.peer_by(c, "id", pid)
    if not p or not F.feature(p, "directory"):
        if p:
            with _db() as c, c:
                if c.execute("SELECT 1 FROM fed_directory WHERE peer_id=? LIMIT 1", (pid,)).fetchone():
                    _drop(c, pid)
        return None
    since, cfg, n = (0 if full else p["dir_seq"]), ("" if full else p["dir_cfg"]), 0
    prior = None                                           # vollstaendiger Abgleich: Stand davor (wer fehlt, ist weg)
    for _ in range(200):                                   # hoechstens 200 Seiten je Durchlauf
        try:
            status, d = F.call_json(p, "GET", "/fed/v2/directory", f"since={int(since)}&cfg={cfg}", timeout=10)
        except F.FedError as ex:
            with _db() as c, c:
                F._touch(c, p, ex)
            return None
        if status == 403:                                  # die andere Seite hat es abgeschaltet
            with _db() as c, c:
                _drop(c, pid)
            return n
        if status != 200 or not isinstance(d, dict) or not isinstance(d.get("entries"), list):
            return None
        now = time.time()
        with _db() as c, c:
            # in der Zwischenzeit getrennt oder abgeschaltet? (Sperre: wartet auf ein laufendes Trennen)
            r = c.execute("SELECT status FROM fed_peer WHERE id=? FOR SHARE", (pid,)).fetchone()
            if not r or not F.feature(F.peer_by(c, "id", pid), "directory"):
                return None
            if d.get("full"):
                gone = c.execute("DELETE FROM fed_directory WHERE peer_id=? RETURNING handle, pub", (pid,)).fetchall()
                if prior is None:
                    prior = dict(gone)
            old = {r[0]: r[1:] for r in c.execute("SELECT handle, name, pub, av FROM fed_directory WHERE peer_id=?", (pid,)).fetchall()}
            for e in d["entries"][:PAGE_MAX]:
                if not isinstance(e, dict) or not isinstance(e.get("handle"), str) or not _H.fullmatch(e["handle"]):
                    continue
                h = e["handle"]
                if e.get("deleted"):
                    if h in old:
                        o = old.pop(h)
                        c.execute("DELETE FROM fed_directory WHERE peer_id=? AND handle=?", (pid, h))
                        avatars.forget_remote(c, principal=_principal({"handle": h, "pub": o[1]}, p["host"]))
                        _forget(c, p["host"], [(h, o[1])])     # (Opt-out, deaktiviert: Name nur noch, wo die Person vorkommt)
                        n += 1
                    continue
                name = F.remote_name(e.get("name"))
                pub = e.get("pub") if isinstance(e.get("pub"), str) and F.NAME_RE.fullmatch(e.get("pub")) else ""
                av = e.get("av") if isinstance(e.get("av"), str) and _AV.fullmatch(e.get("av")) else ""
                if not name:
                    continue
                o = old.get(h)
                if o == (name, pub, av):
                    continue
                c.execute("INSERT INTO fed_directory (peer_id, handle, name, pub, av, updated) VALUES (?,?,?,?,?,?) "
                          "ON CONFLICT (peer_id, handle) DO UPDATE SET name=EXCLUDED.name, pub=EXCLUDED.pub, av=EXCLUDED.av, "
                          "updated=EXCLUDED.updated", (pid, h, name, pub, av, now))
                old[h] = (name, pub, av)
                if o and (o[2] != av or o[1] != pub):          # neues Bild bzw. anderer Name: zwischengespeichertes Bild weg
                    avatars.forget_remote(c, principal=_principal({"handle": h, "pub": o[1]}, p["host"]))
                if pub:
                    F._person_upsert(c, f"@{pub}@{p['host']}", pid, name)
                n += 1
            nxt = d.get("next") if isinstance(d.get("next"), int) and not isinstance(d.get("next"), bool) else 0
            c.execute("UPDATE fed_peer SET dir_seq=?, dir_cfg=?, dir_at=? WHERE id=?",
                      (max(0, nxt), str(d.get("cfg") or "")[:40], now, pid))
        since, cfg = nxt, str(d.get("cfg") or "")[:40]
        if not d.get("more"):
            if prior:
                with _db() as c, c:
                    now_ = {r[0] for r in c.execute("SELECT handle FROM fed_directory WHERE peer_id=?", (pid,)).fetchall()}
                    _forget(c, p["host"], [(h, pub) for h, pub in prior.items() if h not in now_])
            break
    return n


_plock = threading.Lock()
_running, _again = set(), {}     # je Instanz hoechstens ein Abgleich gleichzeitig (Rest: einmal nachholen)


def pull_bg(pid, full=False):
    """Abgleich im Express-Pool - je Instanz hoechstens einer laufend und einer vorgemerkt: eine Flut von
    'directory.changed' (oder eine langsame Gegenstelle) belegt nicht den ganzen Pool."""
    with _plock:
        if pid in _running:
            _again[pid] = _again.get(pid, False) or bool(full)
            return
        _running.add(pid)
    if F.peer_submit(pid, _pull_loop, pid, bool(full)) is False:     # Pool beendet (Herunterfahren)
        with _plock:
            _running.discard(pid)
            _again.pop(pid, None)


def _pull_loop(pid, full):
    try:
        while True:
            pull(pid, full)
            with _plock:
                if pid not in _again:
                    _running.discard(pid)
                    return
                full = _again.pop(pid)
    except BaseException:
        with _plock:
            _running.discard(pid)
            _again.pop(pid, None)
        raise


def _ev_changed(c, peer, ev):
    """'directory.changed': nach dem Commit holen (im Express-Pool, nicht in der Anfrage)."""
    if not F.feature(peer, "directory"):
        return "ignored"
    full = bool(ev.get("full"))
    c.after_commit(lambda: pull_bg(peer["id"], full))
    return "ok"


F.EV2_HANDLERS["directory.changed"] = _ev_changed


def _on_hello(pid):
    with _db() as c:
        p = F.peer_by(c, "id", pid)
    if p and F.feature(p, "directory"):
        refresh_local()
        pull(pid, full=True)
    elif p:
        with _db() as c, c:
            _drop(c, pid)


def _on_opts(pid, old, new):
    """Admin hat Schalter einer Instanz geaendert: eigenes Adressbuch dort neu (andere Filter) bzw. fremdes holen/loeschen."""
    with _db() as c, c:
        p = F.peer_by(c, "id", pid)
        if not p or p["status"] != "active":
            return
        if not new.get("directory"):
            _drop(c, pid)
        if "directory" in F.caps_of(p):
            # die andere Seite holt neu (dort passt cfg nicht mehr bzw. sie bekommt 403 und loescht)
            c.execute("DELETE FROM fed_outbox WHERE peer_id=? AND chain='dir' AND locked_until=0", (pid,))
            F._enqueue(c, pid, {"type": "directory.changed", "full": True}, chain="dir")
    if new.get("directory") and not old.get("directory"):
        pull_bg(pid, True)


F.HELLO_HOOKS.append(_on_hello)
F.OPTS_HOOKS.append(_on_opts)


_cfg_seen = {}               # peer_id -> letzter Filter-Stand (z. B. "nur gemeinsame Gruppen" im Admin umgeschaltet)


def _check_cfg():
    with _db() as c, c:
        for p in F.peers(c):
            if not F.feature(p, "directory"):
                continue
            cf, old = _cfg(p), _cfg_seen.get(p["id"])
            _cfg_seen[p["id"]] = cf
            if old is not None and old != cf:
                _announce(c, only=p["id"], full=True)


def periodic():
    now = time.time()
    with _db() as c:
        if not any(F.feature(p, "directory") for p in F.peers(c)):
            return                           # keine Instanz mit Adressbuch: nichts zu tun (beim Verbinden: _on_hello)
    if now - _st["bg"] >= REFRESH_EVERY:
        _st["bg"] = now
        refresh_local()
        _check_cfg()
    if now - _st["sync"] >= 300:
        _st["sync"] = now
        with _db() as c:
            due = [p["id"] for p in F.peers(c) if F.feature(p, "directory") and (not p["dir_at"] or now - p["dir_at"] > SYNC_EVERY)]
        for pid in due[:10]:
            pull_bg(pid)                     # im Express-Pool, je Instanz begrenzt (eine langsame haelt den Arbeiter nicht auf)


F.PERIODIC.append(periodic)


# =====================================================================================================
# Suche (Chat, Teilen, Erwaehnungen)
# =====================================================================================================
def _like(q):
    return re.sub(r"([\\%_])", r"\\\1", q)


def search(uid, q, purpose="chat"):
    """Personen anderer Instanzen nach Anzeigename (hoechstens SEARCH_MAX): ab 3 Zeichen Teilwort-Suche, bei 1-2 Zeichen nur
    genaue Treffer (Anzeigename, oeffentlicher Name) - nie das ganze Adressbuch. 'name@host' bzw. '@name@host': genau diese
    Person (oeffentlicher Name oder Handle) auf dieser Instanz, auch mit kurzem Namen ('hd@b.example'). purpose 'share': nur
    Instanzen, mit denen Teilen erlaubt ist; Ziel ist der oeffentliche Name bzw. (2.3) der pseudonyme Handle ('@~x@host').
    -> [{type: 'remote', target, handle, name, host, label, avatar}]"""
    q = re.sub(r"\s+", " ", str(q or "")).strip().lower()[:60]
    if not q or not F.enabled():
        return []
    addr = re.fullmatch(r"@?([^@\s]+)@([^@\s]+)", q)
    with _db() as c:
        ps = {p["id"]: p for p in F.peers(c) if F.feature(p, "directory") and (purpose != "share" or F.scope_ok(p["host"]))}
        if not ps:
            return []
        if purpose == "chat" and sharing.group_scope():
            ps = {k: p for k, p in ps.items() if p["opts"].get("scope_exception")}
            if not ps:
                return []
        pat = _like(q)
        if addr:                                       # genaue Adresse: nur diese Instanz, nur genau dieser Name
            ps = {k: p for k, p in ps.items() if p["host"] == fedsig.authority(addr.group(2))}
            if not ps:
                return []
            cond, args = "(pub=? OR handle=?)", (addr.group(1), addr.group(1))
        elif len(q) < sharing.SHORT_Q:
            cond, args = "(lower(name)=? OR pub=?)", (q, q)
        else:
            cond, args = "(lower(name) LIKE ? OR pub LIKE ?)", ("%" + pat + "%", pat + "%")
        sql = (f"SELECT peer_id, handle, name, pub, av FROM fed_directory WHERE peer_id IN ({','.join('?' * len(ps))}) AND " + cond +
               " ORDER BY (lower(name)=?) DESC, (lower(name) LIKE ?) DESC, name, peer_id, handle LIMIT ?")
        rows = c.execute(sql, (*ps, *args, q, pat + "%", SEARCH_MAX)).fetchall()
        hits = []
        for pid, h, name, pub, av in rows:
            p = ps[pid]
            hits.append({"type": "remote", "target": f"@{pub or h}@{p['host']}", "handle": f"{h}@{p['host']}", "name": name, "host": p["host"],
                         "pub": f"{pub}@{p['host']}" if pub else None, "av": av})
        urls = avatars.urls(c, [x["target"] for x in hits if x["av"]]) if hits else {}
    labels = {}
    for x in hits:
        labels.setdefault((x["name"].lower(), x["host"]), []).append(x)
    for x in hits:
        x["label"] = f"{x['name']} · {x['host']}"
        if len(labels[(x["name"].lower(), x["host"])]) > 1:      # gleicher Name auf derselben Instanz: unterscheidbar machen
            x["label"] += " · " + (x["pub"].split("@")[0] if x["pub"] else x["handle"].split("@")[0][:7] + "…")
        x["avatar"] = urls.get(x["target"])
        x.pop("av", None)
    return hits


sharing.DIR_SEARCH = search


_people_hits = {}    # uid -> [zeitstempel]


@router.get("/api/fed/people")
def api_people(request: Request, q: str = "", purpose: str = "chat"):
    """Adressbuch-Suche fuer Apps (Chat, Teilen, Erwaehnungen): Personen anderer Instanzen nach Anzeigename."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    now = time.time()                                   # wie die anderen Personensuchen: hoechstens 60 Anfragen je Minute
    hits = [x for x in _people_hits.get(uid, []) if now - x < 60]
    if len(hits) >= 60:
        return _bad(t("zu schnell"), 429)
    _people_hits[uid] = hits + [now]
    if len(_people_hits) > 5000:
        _people_hits.clear()
    return JSONResponse({"people": search(uid, q, "share" if purpose == "share" else "chat")}, headers={"Cache-Control": "no-store"})


# =====================================================================================================
# Schalter je Konto (Einstellungen -> Konto)
# =====================================================================================================
def account_state(uid):
    with _db() as c:
        on = account_on(c, uid)
        h = c.execute("SELECT handle FROM fed_handle WHERE uid=?", (uid,)).fetchone()
        n = sum(1 for p in F.peers(c) if F.feature(p, "directory"))
    return {"directory": on, "federation": F.enabled(), "peers": n, "handle": f"{h[0]}@{F.SELF_HOST}" if h else None}


@router.get("/api/account/federation")
def api_account_get(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    return JSONResponse(account_state(uid), headers={"Cache-Control": "no-store"})


@router.put("/api/account/federation")
async def api_account_put(request: Request):
    """{directory: bool} - "In Adressbuechern verbundener Server erscheinen" (Standard: an)."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request)
    if not isinstance(b, dict) or not isinstance(b.get("directory"), bool):
        return _bad(t("ungültige Angabe"))

    def run():
        with _db() as c, c:
            if b["directory"]:
                c.execute("DELETE FROM user_kv WHERE uid=? AND k=?", (uid, KV))
            else:
                c.execute("INSERT INTO user_kv (uid, k, v) VALUES (?,?,'0') ON CONFLICT (uid, k) DO UPDATE SET v='0'", (uid, KV))
        refresh_local(force=True)                          # sofort wirksam (Grabstein bzw. Eintrag + Ereignis)
        return account_state(uid)
    return JSONResponse(await run_in_threadpool(run), headers={"Cache-Control": "no-store"})

