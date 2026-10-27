"""Teilen ueber Servergrenzen vollstaendig (2.3, Etappen E4 und E6): Datenbanken, Unterseiten, Bilder hochladen,
Versionen, Export, Anwesenheit und Live fuer Seiten anderer Saga-Instanzen.

Muster (wie Protokoll 1, saga.federation): Die Weboberflaeche/App spricht nur mit dem eigenen Server (Heim) und benutzt
die gewohnten Endpunkte mit negativen IDs (-(id_dort * 1024 + peer_id)). Das Heim ruft im Namen der Person signiert
/fed/v2/notes/{id}/... beim Eigentuemer-Server auf; der fuehrt den LOKALEN Handler mit dem Prinzipal '@name@host' aus -
die Rolle prueft also immer der Eigentuemer (sharing.Access, Gruppen signiert bestaetigt). IDs in Anfragen und Antworten
rechnet das Heim um (Seiten, Zeilen, Relationen, Ziel-Datenbanken, Bilder), Personen kommen nie mit Anmeldenamen.

Funktionen nur, wenn beide Server die Faehigkeit melden (db subpages upload versions export presence live):
- db: Datenbank oeffnen (Schema, Ansichten), Zeilen mit Filter/Sortierung/Suche/Gruppen/Blaettern (GET .../rows),
  Werte und Schema bearbeiten (PATCH mit props/schema), Zeilen anlegen (= Unterseiten mit props).
- subpages: Unterseiten anlegen (POST /api/notes mit negativer parent_id) und innerhalb des geteilten Bereichs
  verschieben (pages.note_move: gleiche Freigaben ueber alter und neuer Stelle).
- upload: Bilder/Dateien liegen beim Eigentuemer und zaehlen bei ihm; je fremder Person zusaetzlich eine eigene Grenze
  (FED_UPLOAD_QUOTA_MB, Standard 200). Gleich nach dem Hochladen sieht nur die hochladende Person die Datei (Vorschau),
  nach dem Speichern alle mit Zugriff auf die Seite.
- versions / export: wie lokal (Versionen ab 'Bearbeiten', Export ab 'Lesen').
- presence + live: kurzlebiges Abo (FED_WATCH_TTL, Standard 90 s) beim Eigentuemer, erneuert mit jeder
  Anwesenheitsmeldung der Seite (alle ~20 s). Solange es laeuft, meldet der Eigentuemer Aenderungen, Anwesenheit und
  Kommentar-Zaehler an das Heim (POST /fed/v2/live, synchron und verwerfbar, ohne Inhalt); das Heim reicht sie als
  normale Live-Ereignisse an genau die Person weiter. Aenderungen am Seitenbaum geteilter Bereiche ('tree') gehen an
  alle Server mit Freigaben dort -> "Mit mir geteilt" neu laden.
Aeltere Gegenstellen (2.1/2.2) bleiben bei Protokoll 1 (Lesen, Bearbeiten von Titel/Inhalt, Kommentare, Dateien lesen)."""
import json
import os
import re
import threading
import time
from urllib.parse import quote, unquote, urlencode

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from db import connect as _db
from saga import app_api, avatars, bus, csv_export, databases, dbcalc, files, live, nextcloud, notify, page_tools, page_widgets, pages, sharing
from saga import federation as F
from saga.comments import counts as comment_counts
from saga.core import E, _bad, _body, t
from saga.files import FILE_MAX, _sniff_image
from saga.notes import _disposition, note_chain

router = APIRouter()

WATCH_TTL = max(5, int(E.get("FED_WATCH_TTL", "90")))              # Abo fuer Live/Anwesenheit (Sekunden)
UPLOAD_QUOTA = max(1, int(E.get("FED_UPLOAD_QUOTA_MB", "200"))) * 1024 * 1024    # je fremder Person und Eigentuemer
EXPORT_MAX = 64 * 1024 * 1024
TREE_GAP = 2.0                       # Baum-Meldungen je Instanz hoechstens alle 2 s (gesammelt)
LIVE_MAX = 50                        # Eintraege je POST /fed/v2/live
_NAME = re.compile(r"[^\x00-\x1f/\\]{1,120}")


def _caps(p, cap):
    return F.feature(p, cap)


# =====================================================================================================
# Eigentuemer-Server: /fed/v2/notes/{id}/... im Namen der fremden Person
# =====================================================================================================
_W = {}            # note_id -> {prinzipal: {"peer", "exp", "editing", "groups", "name"}}  (Abos fremder Personen)
_WL = threading.Lock()


def _watchers(nid, now=None):
    now = now or time.time()
    with _WL:
        return {k: dict(v) for k, v in (_W.get(nid) or {}).items() if v["exp"] > now}


def _role_as(c, principal, groups, nid):
    tok = sharing.REMOTE_GROUPS.set({principal: frozenset(groups)})
    try:
        return sharing.Access(c, principal).role(nid)
    finally:
        sharing.REMOTE_GROUPS.reset(tok)


def _ext_user(c, u, cache):
    """Person fuer eine andere Instanz (nie der Anmeldename): {name, username}."""
    return F._ext_persons(c, {"name": "", "username": u, "me": False}, cache)


def _present(c, nid, pres, skip=None):
    """Anwesende einer Seite fuer eine andere Instanz: lokale Konten (mit Zugriff) + Abos fremder Personen."""
    cache, out = {}, []
    entries = (pres or {}).get(nid) or {}
    acc_ok = [u for u in sorted(entries) if sharing.Access(c, u).role(nid)]
    for u in acc_ok:
        e = _ext_user(c, u, cache)
        # D01: an andere Server nie der (uebersetzte) Ersatz oder die lokale Kennung - Anzeigename, sonst wie _ext_user
        e.update(name=F.display_of(c, u) or e.get("name") or "?", editing=bool(entries[u]))
        out.append(e)
    for pr, w in _watchers(nid).items():
        if pr == skip or not _role_as(c, pr, w["groups"], nid):       # Freigabe inzwischen entzogen: nicht mehr anzeigen
            continue
        out.append({"name": w["name"], "username": pr, "editing": bool(w["editing"])})
    return out


def watchers_for_local(c, nid, me):
    """live.FED_WATCHERS: fremde Personen auf der Seite (fuer die Anwesenheit lokaler Konten)."""
    out = []
    for pr, w in _watchers(nid).items():
        if not _role_as(c, pr, w["groups"], nid):
            continue
        out.append({"name": sharing.remote_label(pr, w["name"]), "username": pr, "editing": bool(w["editing"]), "remote": True,
                    "avatar": avatars.url_of(c, pr), "me": False})
    return out


def _signed_resp(inp, resp):
    """Antwort eines lokalen Handlers (JSON) signiert an die anfragende Instanz - Personen umgeschrieben."""
    if isinstance(resp, JSONResponse):
        return F._relay(inp, resp)
    return F._signed(inp, getattr(resp, "status_code", 500), {"error": "unexpected"})


def _fx_legacy(inp, resp, nid=None):
    """Gegenstelle ohne formula1 (aeltere Saga): Formel/Rollup als nur-lesbare Text-Spalte (dbcalc.legacy_*), damit deren
    Weboberflaeche den unbekannten Typ nicht sieht und die Werte als Text zeigt. Mit formula1: unveraendert."""
    if _caps(inp.peer, "formula1") or not isinstance(resp, JSONResponse) or resp.status_code != 200:
        return resp
    try:
        d = json.loads(resp.body)
    except ValueError:
        return resp
    if not isinstance(d, dict):
        return resp
    sch = d.get("schema")
    if isinstance(sch, dict) and dbcalc.has_calc(sch):
        d["schema"] = dbcalc.legacy_schema(sch)
        if isinstance(d.get("rows"), list):
            dbcalc.legacy_rows(sch, d["rows"])
        if any(isinstance(g, dict) and "value" in g for g in d.get("groups") or []):
            d.pop("groups", None)                  # Gruppen nach Formel kennt die Gegenstelle nicht
    if nid is not None and isinstance(d.get("props"), dict):          # Einzelzeile: Eltern-Schema bestimmt die Spalten
        with _db() as c:
            r = c.execute("SELECT d.schema FROM note n JOIN note d ON d.id=n.parent_id AND d.uid=n.uid WHERE n.id=? "
                          "AND d.kind='database'", (nid,)).fetchone()
        ps = json.loads(r[0]) if r and r[0] else None
        if dbcalc.has_calc(ps):
            dbcalc.legacy_rows(ps, [d])
    d.pop("calc", None)
    d.pop("fx_err", None)
    return JSONResponse(d, status_code=200, headers={k: v for k, v in resp.headers.items() if k.lower() == "cache-control"})


def _need(inp, cap):
    return None if _caps(inp.peer, cap) else F._signed(inp, 403, {"error": f"{cap} disabled"})


async def _owner_req(request, nid, rest):
    raw = request.method == "POST" and rest == "files"
    inp, err = await F._verify(request, need_actor=True, raw=raw)
    if err is not None:
        return err
    if not 0 < nid < 2 ** 63:
        return F._signed(inp, 404, {"error": "not found"})
    m, b = request.method, (inp.data if isinstance(inp.data, dict) else {})
    q = dict(request.query_params)
    mv = re.fullmatch(r"versions/([1-9][0-9]{0,17})(/restore)?", rest)
    if m == "GET" and rest == "":
        return _need(inp, "db") or _signed_resp(inp, _fx_legacy(inp, await run_in_threadpool(F._as, inp, pages.note_get, nid), nid))
    if m == "PATCH" and rest == "":
        if _need(inp, "db"):
            return _need(inp, "db")
        b = {k: v for k, v in b.items() if k in F.EDIT2_KEYS}
        if not b:
            return F._signed(inp, 400, {"error": t("nichts zu ändern")})
        return _signed_resp(inp, _fx_legacy(inp, await run_in_threadpool(F._as, inp, pages._note_update, nid, b, None,
                                                                         F.peer_caps(inp.peer))))
    if m == "POST" and rest == "children":
        if _need(inp, "subpages"):
            return _need(inp, "subpages")
        b = {k: v for k, v in b.items() if k in ("title", "icon", "tags", "kind", "props")}
        if isinstance(b.get("icon"), str) and b["icon"].startswith("/") and not re.fullmatch(r"/files/[0-9a-f]{24}", b["icon"]):
            b.pop("icon")
        return _signed_resp(inp, await run_in_threadpool(F._as, inp, pages.note_create, dict(b, parent_id=nid)))
    if m == "POST" and rest == "duplicate":
        # tplbtn (2.5): Schaltflaeche "Neuer Eintrag" mit Vorlage auf einer hier liegenden, foederiert geteilten Seite -
        # nid = Vorlage, via = Seite mit der Schaltflaeche, parent_id = Ziel-Datenbank (alles IDs hier). Pruefung wie
        # lokal (databases._via_button: Signatur des Eigentuemers, Ziel bearbeiten); nie als Vorlage.
        # rowtpl (fix27 E1): ohne via = Eintrags-Vorlage in ihre Datenbank parent_id (ab 'Bearbeiten' dort, wie lokal)
        if _need(inp, "rowtpl" if b.get("via") is None else "tplbtn"):
            return _need(inp, "rowtpl" if b.get("via") is None else "tplbtn")
        out = {}
        for k in ("parent_id", "via"):
            v = b.get(k)
            if k == "via" and v is None:
                continue
            if not (isinstance(v, int) and not isinstance(v, bool) and 0 < v < 2 ** 63):
                return F._signed(inp, 404, {"error": "not found"})
            out[k] = v
        if isinstance(b.get("title"), str):
            out["title"] = b["title"]
        return _signed_resp(inp, await run_in_threadpool(F._as, inp, databases.note_duplicate, nid, out))
    if m == "POST" and rest == "discard":             # fix27 E4: unveraenderten neuen Eintrag verwerfen (nur wer ihn anlegte)
        if _need(inp, "rowtpl"):
            return _need(inp, "rowtpl")
        tok = b.get("fresh") if isinstance(b.get("fresh"), str) else ""
        return _signed_resp(inp, await run_in_threadpool(F._as, inp, databases.note_discard, nid, {"fresh": tok[:64]}))
    if m == "POST" and rest == "move":
        if _need(inp, "subpages"):
            return _need(inp, "subpages")
        par = b.get("parent_id")
        if not (isinstance(par, int) and not isinstance(par, bool) and 0 < par < 2 ** 63):
            return F._signed(inp, 403, {"error": t("Dafür fehlt dir die Berechtigung.")})
        return _signed_resp(inp, await run_in_threadpool(F._as, inp, pages.note_move, nid, {"parent_id": par, "index": b.get("index", 0)}))
    if m == "GET" and rest == "rows":
        return _need(inp, "db") or _signed_resp(inp, _fx_legacy(inp, await run_in_threadpool(F._as, inp, app_api.db_rows, nid, q)))
    if m == "POST" and rest == "formula/check":      # 2.5 (formula1): Formel-Editor fuer eine hier liegende Datenbank
        if _need(inp, "formula1"):
            return _need(inp, "formula1")
        b = {k: b[k] for k in ("expr", "pid", "names") if k in b}
        return _signed_resp(inp, await run_in_threadpool(F._as, inp, databases.formula_check_api, nid, b))
    if m == "GET" and rest == "widgets":             # 2.5: Widgets mit den Rechten der fremden Person (saga.page_widgets)
        return _need(inp, "widgets1") or _signed_resp(inp, await run_in_threadpool(F._as, inp, page_widgets.page_widgets, nid, q,
                                                                                   _caps(inp.peer, "widgets2")))
    mh = re.fullmatch(r"widgets/([A-Za-z0-9_-]{1,40})/habit", rest)
    if m == "POST" and mh:                           # 2.5 Paket 4 (widgets2): Gewohnheit abhaken (Rechte der fremden Person)
        if _need(inp, "widgets2"):
            return _need(inp, "widgets2")
        body = {k: b.get(k) for k in ("pid", "day", "on")}
        return _signed_resp(inp, await run_in_threadpool(F._as, inp, page_widgets.habit_check, nid, mh.group(1), body, q))
    if m == "GET" and rest == "chart":               # 2.5 Paket 4 (widgets2): Diagramm-Ansicht einer Datenbank
        return _need(inp, "widgets2") or _signed_resp(inp, await run_in_threadpool(F._as, inp, page_widgets.db_chart, nid, q))
    if m == "GET" and rest == "versions":
        return _need(inp, "versions") or _signed_resp(inp, await run_in_threadpool(F._as, inp, page_tools.versions_list, nid))
    if mv and m == "GET" and not mv.group(2):
        return _need(inp, "versions") or _signed_resp(inp, await run_in_threadpool(F._as, inp, page_tools.version_get, nid, int(mv.group(1))))
    if mv and m == "POST" and mv.group(2):
        return _need(inp, "versions") or _signed_resp(inp, await run_in_threadpool(F._as, inp, page_tools.version_restore, nid, int(mv.group(1))))
    if m == "GET" and rest == "export":
        if _need(inp, "export"):
            return _need(inp, "export")
        return await run_in_threadpool(_export_owner, inp, nid, 1 if q.get("sub") == "1" else 0)
    if m == "GET" and rest == "export.csv":          # 2.5 (Paket 2): Datenbank als CSV mit den Rechten der Person
        if _need(inp, "csv"):
            return _need(inp, "csv")
        return await run_in_threadpool(_csv_owner, inp, nid, q)
    if m == "POST" and rest == "files":
        if _need(inp, "upload"):
            return _need(inp, "upload")
        return await run_in_threadpool(_upload_owner, inp, nid, q.get("name") or "", q.get("image") == "1")
    if m == "POST" and rest == "files/copy":
        if _need(inp, "upload"):
            return _need(inp, "upload")
        return _signed_resp(inp, await run_in_threadpool(F._as, inp, _copy_owner, str(b.get("url") or ""), nid))
    mn = re.fullmatch(r"nc(?:/([1-9][0-9]{0,19}))?", rest)
    if mn and m == "GET":
        if _need(inp, "nc_files"):
            return _need(inp, "nc_files")
        return await run_in_threadpool(_nc_owner, inp, nid, mn.group(1), q)
    if m == "POST" and rest == "presence":
        if _need(inp, "presence"):
            return _need(inp, "presence")
        return await run_in_threadpool(_presence_owner, inp, nid, b.get("editing") is True, b.get("leave") is True)
    return F._signed(inp, 404, {"error": "not found"})


def _nc_owner(inp, nid, fileid, q):
    """Nextcloud-Dateien einer Seite fuer eine fremde Person: nur Bloecke mit Schalter "fed" (saga.nextcloud).
    Ohne Datei-ID: Status aller solchen Bloecke; mit: Vorschau (w) bzw. Datei (dl=1, gepuffert bis NC_FED_MAX_MB)."""
    def run(principal):
        with _db() as c:
            if fileid is None:
                plan = nextcloud.fed_status_plan(c, principal, nid)
            else:
                d, acct = nextcloud.fed_locate(c, principal, nid, fileid, str(q.get("a") or ""))
        if fileid is None:
            if plan is None:
                return None
            out = nextcloud.status_run(plan)
            for it in out["items"]:
                it["author"] = nextcloud.pseudo(it["author"]) if it.get("author") else ""
            return ("json", out)
        if not d or not acct:
            return None
        try:
            w = int(q.get("w") or 0)
        except ValueError:
            w = 0
        resp = nextcloud.deliver(acct, d, "download" if q.get("dl") == "1" else "preview", w, buffered_max=nextcloud.FED_MAX)
        if resp.status_code != 200:
            return None
        return ("raw", resp.body, resp.media_type, resp.headers.get("content-disposition") or "")
    got = F._as(inp, run)
    if not got:
        return F._signed(inp, 404, {"error": "not found"})
    if got[0] == "json":
        return F._signed(inp, 200, got[1])
    return F._signed(inp, 200, got[1], got[2], {"Content-Disposition": got[3]} if got[3] else None)


@router.api_route("/fed/v2/notes/{nid}", methods=["GET", "PATCH"])
async def fed_note2(nid: int, request: Request):
    return await _owner_req(request, nid, "")


@router.api_route("/fed/v2/notes/{nid}/{rest:path}", methods=["GET", "POST"])
async def fed_note2_sub(nid: int, rest: str, request: Request):
    return await _owner_req(request, nid, rest)


def _export_owner(inp, nid, sub):
    def run(principal):
        return page_tools.note_export(principal, nid, sub)
    resp = F._as(inp, run)
    if isinstance(resp, JSONResponse):
        return F._relay(inp, resp)
    path = getattr(resp, "path", None)
    try:
        if path:
            if os.path.getsize(path) > EXPORT_MAX:
                return F._signed(inp, 413, {"error": t("Export zu groß")})
            with open(path, "rb") as fh:
                data = fh.read()
        else:
            data = resp.body
    finally:
        if path:
            try:
                os.remove(path)
            except OSError:
                pass
    media = "application/zip" if path else "text/markdown; charset=utf-8"
    return F._signed(inp, 200, data, media, {"Content-Disposition": resp.headers.get("content-disposition", "attachment")})


def _csv_owner(inp, nid, q):
    res = F._as(inp, csv_export.build, nid, q)
    if not isinstance(res, tuple):
        return F._relay(inp, res) if isinstance(res, JSONResponse) else F._signed(inp, 500, {"error": "unexpected"})
    name, data = res
    if len(data) > EXPORT_MAX:
        return F._signed(inp, 413, {"error": t("Export zu groß")})
    return F._signed(inp, 200, data, "text/csv; charset=utf-8", {"Content-Disposition": _disposition(name)})


def _upload_owner(inp, nid, name, img_field):
    data = inp.body or b""
    name = name.strip()[:120]
    if not _NAME.fullmatch(name or "-"):
        name = "datei"
    if not data:
        return F._signed(inp, 400, {"success": 0, "error": t("leere Datei")})
    if len(data) > FILE_MAX:
        return F._signed(inp, 413, {"success": 0, "error": t("Datei zu groß (max. {n} MB)", n=FILE_MAX // 1048576)})
    img = _sniff_image(data[:16])
    if img_field and not img:
        return F._signed(inp, 415, {"success": 0, "error": t("kein unterstütztes Bild (PNG, JPEG, GIF, WebP)")})

    def run(principal):
        owner, err = files.upload_owner(principal, nid)
        if err is not None:
            return err
        out, err = files.store_upload(owner, name or "datei", data, img, fed_by=principal, fed_note=nid, fed_quota=UPLOAD_QUOTA)
        if err is None:
            print(f"fed_pages: Upload {len(data)} Bytes einer Person von {inp.peer['host']} in Seite {nid}", flush=True)
        return err if err is not None else JSONResponse(out)
    return F._relay(inp, F._as(inp, run))


def _copy_owner(principal, url, nid):
    """Kopie einer Datei in die Seite: zaehlt wie ein Upload auch bei der Grenze der fremden Person."""
    return files.file_copy(principal, url, nid, fed_quota=UPLOAD_QUOTA)


def _presence_owner(inp, nid, editing, leave):
    """Abo einer fremden Person fuer eine Seite hier (Live + Anwesenheit, WATCH_TTL). -> Stand der Seite + Anwesende."""
    pr, now = inp.principal, time.time()
    with _db() as c:
        role = _role_as(c, pr, inp.groups, nid)
        r = c.execute("SELECT rev, updated, updated_by FROM note WHERE id=? AND deleted_at IS NULL", (nid,)).fetchone() if role else None
        dn = c.execute("SELECT display FROM fed_person WHERE principal=?", (pr,)).fetchone()
    changed = False
    with _WL:
        cur = _W.setdefault(nid, {})
        old = cur.get(pr)
        if leave or not r:
            changed = cur.pop(pr, None) is not None
        else:
            cur[pr] = {"peer": inp.peer["id"], "exp": now + WATCH_TTL, "editing": editing, "groups": frozenset(inp.groups),
                       "name": F._clean_text((dn[0] if dn else "") or pr[1:].split("@")[0], F.DISPLAY_MAX)}
            changed = old is None or old["exp"] <= now or bool(old["editing"]) != editing
        if not cur:
            _W.pop(nid, None)
    if changed:
        bus.emit("presence", {"note_id": nid})
    if leave:
        return F._signed(inp, 200, {"ok": True})
    if not r:
        return F._signed(inp, 404, {"error": "not found"})
    try:
        pres = {nid: {u: bool(e[1]) for u, e in list((live._presence.get(nid) or {}).items())}}
    except RuntimeError:                     # gleichzeitig veraendert (Event-Schleife): ohne lokale Anwesende
        pres = {}
    with _db() as c:
        users = _present(c, nid, pres, skip=pr)
        cache = {}
        by = _ext_user(c, r[2], cache) if r[2] and r[2] != pr else None
    users.append({"name": "", "username": pr, "editing": editing})
    return F._signed(inp, 200, {"rev": r[0], "updated": r[1], "by": by, "users": users, "ttl": live.PRESENCE_TTL, "watch": WATCH_TTL})


# --- Weitergabe an die Abos (live.FED_FANOUT, im Thread) ---------------------------------------------------------------
_tree_last, _tree_pend = {}, {}
_TL = threading.Lock()


def _post_live(pid, items):
    def go():
        with _db() as c:
            p = F.peer_by(c, "id", pid)
        if not _caps(p, "live"):
            return
        for k in range(0, len(items), LIVE_MAX):
            try:
                F.call_json(p, "POST", "/fed/v2/live", body={"items": items[k:k + LIVE_MAX]}, timeout=max(1.0, F.EXPRESS_TIMEOUT / 3))
            except F.FedError:
                return
    F.peer_submit(pid, go, drop=True)        # je Instanz begrenzt - ausgelastet: Hinweis faellt weg (verwerfbar)


def fanout(kind, p, pres):
    """Seiten-Ereignis -> an die Abos fremder Personen (gleich, ohne Ausgangskorb)."""
    if not F.enabled():
        return
    if kind == "notes.tree":
        return _tree_fanout(p)
    nid = p.get("id") if kind == "note.changed" else p.get("note_id")
    if not isinstance(nid, int):
        return
    ws = _watchers(nid)
    if not ws:
        return
    by_peer = {}
    with _db() as c:
        cache = {}
        if kind == "note.changed":
            by = p.get("by")
            byx = _ext_user(c, by, cache) if by else None
            for pr, w in ws.items():
                if pr == by and not p.get("origin"):
                    continue
                if p.get("gone"):
                    it = {"type": "note.changed", "note": nid, "gone": True, "updated": p.get("updated")}
                elif _role_as(c, pr, w["groups"], nid):
                    it = {"type": "note.changed", "note": nid, "rev": p.get("rev"), "updated": p.get("updated"), "by": byx}
                    if p.get("row"):
                        it["row"] = p["row"] if _role_as(c, pr, w["groups"], p["row"]) else True
                else:
                    continue
                by_peer.setdefault(w["peer"], []).append(dict(it, to=pr[1:].split("@")[0]))
        elif kind == "presence":
            users = _present(c, nid, pres)
            for pr, w in ws.items():
                if _role_as(c, pr, w["groups"], nid):
                    by_peer.setdefault(w["peer"], []).append({"type": "presence", "note": nid, "to": pr[1:].split("@")[0], "users": users})
        elif kind == "comments.changed":
            if p.get("only") and not sharing.is_remote(p["only"]):
                return
            for pr, w in ws.items():
                if p.get("only") and p["only"] != pr:
                    continue
                tok = sharing.REMOTE_GROUPS.set({pr: frozenset(w["groups"])})
                try:
                    if not sharing.Access(c, pr).role(nid):
                        continue
                    per, n_open, n_unread = comment_counts(c, pr, nid)
                finally:
                    sharing.REMOTE_GROUPS.reset(tok)
                by_peer.setdefault(w["peer"], []).append({"type": "comments.changed", "note": nid, "to": pr[1:].split("@")[0],
                                                          "counts": {"blocks": per, "open": n_open, "unread": n_unread}})
    for pid, items in by_peer.items():
        _post_live(pid, items)


def _tree_fanout(p):
    """Seitenbaum geaendert: Server mit Freigaben (Personen/Gruppen dort) in der Kette -> 'Mit mir geteilt' neu laden."""
    owner, ids = p.get("owner"), p.get("ids") or []
    if not owner or sharing.is_remote(owner) or not ids:
        return
    with _db() as c:
        if not c.execute("SELECT 1 FROM note_share WHERE owner_uid=? AND target_type IN ('remote','remote_group') LIMIT 1", (owner,)).fetchone():
            return
        chain = set()
        for i in ids[:200]:
            chain.update(note_chain(c, owner, i) or [])
            r = c.execute("SELECT parent_id FROM note WHERE id=? AND uid=?", (i, owner)).fetchone()
            if r and r[0]:                                   # geloescht/archiviert: Kette der Elternseite
                chain.update(note_chain(c, owner, r[0]) or [])
        if not chain:
            return
        rows = c.execute(f"SELECT DISTINCT target_type, target FROM note_share WHERE owner_uid=? AND target_type IN ('remote','remote_group') "
                         f"AND note_id IN ({','.join('?' * len(chain))})", (owner, *sorted(chain))).fetchall()
        by_peer = {}
        for tt, tg in rows:
            pp, name = F._target_peer(c, tg)
            if pp and _caps(pp, "live"):
                d = by_peer.setdefault(pp["id"], {"users": set(), "groups": set()})
                d["users" if tt == "remote" else "groups"].add(name)
    now = time.monotonic()
    for pid, d in by_peer.items():
        with _TL:
            pend = _tree_pend.get(pid)
            if pend is not None:                             # schon vorgemerkt: sammeln
                pend["users"] |= d["users"]
                pend["groups"] |= d["groups"]
                continue
            wait = TREE_GAP - (now - _tree_last.get(pid, -TREE_GAP))
            if wait > 0:
                _tree_pend[pid] = d
                threading.Timer(wait, _tree_flush, (pid,)).start()
                continue
            _tree_last[pid] = now
        _post_live(pid, [{"type": "tree", "users": sorted(d["users"]), "groups": sorted(d["groups"])}])


def _tree_flush(pid):
    with _TL:
        d = _tree_pend.pop(pid, None)
        _tree_last[pid] = time.monotonic()
    if d:
        _post_live(pid, [{"type": "tree", "users": sorted(d["users"]), "groups": sorted(d["groups"])}])


def periodic():
    """Abgelaufene Abos: aus der Anwesenheit nehmen (Live-Ereignis an die anderen)."""
    now, gone = time.time(), []
    with _WL:
        for nid in list(_W):
            for pr in [k for k, v in _W[nid].items() if v["exp"] <= now]:
                _W[nid].pop(pr, None)
                gone.append(nid)
            if not _W[nid]:
                _W.pop(nid, None)
    for nid in set(gone):
        bus.emit("presence", {"note_id": nid})


def _peer_gone(c, p):
    with _WL:
        for nid in list(_W):
            for pr in [k for k, v in _W[nid].items() if v["peer"] == p["id"]]:
                _W[nid].pop(pr, None)
            if not _W[nid]:
                _W.pop(nid, None)
    with _HL:
        for k in [k for k in _H if k[0] == p["id"]]:
            _H.pop(k, None)


# =====================================================================================================
# Heim-Server: Weboberflaeche/Apps -> Seite einer anderen Instanz
# =====================================================================================================
_H = {}            # (peer_id, id_dort) -> {uid: ablauf}   (Abos der eigenen Konten)
_HL = threading.Lock()


def _peer_for(uid, nid, cap):
    """-> (peer, rid) oder F._Fail. Faehigkeit cap muss die Gegenstelle koennen."""
    pid, rid = F.unref(nid)
    with _db() as c:
        p = F._home_peer(c, pid)
    if not _caps(p, cap):
        raise F._Fail(_bad(t("Für Seiten anderer Saga-Instanzen nicht verfügbar.") + " "
                           + t("Der Server {host} kann das noch nicht (ältere Saga-Version).", host=p["host"]), 404))
    return p, rid


def _call(uid, p, method, path, query="", body=None, limit=None, raw=False):
    """Signiert im Namen von uid -> (status, kopf, daten|json). Nicht erreichbar -> F._Fail(502)."""
    with _db() as c:
        actor = F._home_actor(c, uid, p["id"])
    try:
        if raw or limit:
            status, rh, data = F.call(p, method, path, query, body, actor, limit=limit or F.RESP_MAX)
            return status, rh, data
        status, d = F.call_json(p, method, path, query, body, actor)
        return status, None, d
    except F.FedError as ex:
        with _db() as c, c:
            F._touch(c, p, ex)
        raise F._Fail(F._unavailable())


def _row_in(c, p, d, uid):
    pid = p["id"]
    if not isinstance(d, dict) or not F._ok_rid(d.get("id")):
        return None
    r = F.ref(pid, d["id"])
    return {"id": r, "title": F._clean_text(d.get("title"), 500), "icon": F._rw_in(F._safe_pic(d.get("icon"), True), pid, r),
            "props": F.props_in(pid, d.get("props") if isinstance(d.get("props"), dict) else {}),
            "updated": F._num(d.get("updated")), "created": F._num(d.get("created")),
            "preview": F._rw_in(F._safe_pic(d.get("preview"), False), pid, r), **_fx_err(d)}


def _fx_err(d):
    e = F.fx_err_in(d.get("fx_err"))
    return {"fx_err": e} if e else {}


def _group_in(g):
    """Gruppe aus GET .../rows: Auswahl {id, count} bzw. (formula1) Formel-Wert {id, value, count}."""
    out = {"id": g.get("id") if isinstance(g.get("id"), str) else None, "count": g.get("count") if isinstance(g.get("count"), int) else 0}
    v = g.get("value")
    if "value" in g and (v is None or isinstance(v, (bool, str)) or (isinstance(v, (int, float)) and v == v and abs(v) < 1e300)):
        out["value"] = v[:10000] if isinstance(v, str) else v
        if isinstance(out["id"], str):
            out["id"] = out["id"][:10000]
    return out


def _tpl_in(p, d):
    """Eintrags-Vorlage aus GET .../rows der anderen Instanz (fix27 E1, ab 'Bearbeiten'): nur id/title/icon."""
    if not isinstance(d, dict) or not F._ok_rid(d.get("id")):
        return None
    r = F.ref(p["id"], d["id"])
    return {"id": r, "title": F._clean_text(d.get("title"), 500), "icon": F._rw_in(F._safe_pic(d.get("icon"), True), p["id"], r)}


def _w_rows(uid, nid, query):
    p, rid = _peer_for(uid, nid, "db")
    status, _, d = _call(uid, p, "GET", f"/fed/v2/notes/{rid}/rows", query)
    if status != 200 or not isinstance(d, dict):
        return F._pass_err(p, status, d)
    with _db() as c:
        rows = [x for x in (_row_in(c, p, r, uid) for r in (d.get("rows") or [])[:5000]) if x]
        tpls = [x for x in (_tpl_in(p, x) for x in (d.get("templates") or [])[:200]) if x] if isinstance(d.get("templates"), list) else []
    out = {"schema": F.schema_in(p["id"], d.get("schema")), "templates": tpls, "rows": rows,
           "role": d.get("role") if d.get("role") in sharing.ROLES else "view"}
    for k in ("total", "matched", "offset", "limit"):
        if isinstance(d.get(k), int) and not isinstance(d.get(k), bool):
            out[k] = d[k]
    if isinstance(d.get("groups"), list):
        out["groups"] = [_group_in(g) for g in d["groups"][:1000] if isinstance(g, dict)]
    return F._json(200, out)


_WBID = re.compile(r"[A-Za-z0-9_-]{1,40}")


def _w_widget_in(w):
    """Wert eines Widgets von der anderen Instanz: nur die bekannten Felder, Typen geprueft, Text gekuerzt."""
    if not isinstance(w, dict):
        return None
    num = lambda v: v if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) < 1e15 else None  # noqa: E731
    return {"kind": w.get("kind") if isinstance(w.get("kind"), str) and len(w["kind"]) <= 20 else None,
            "value": num(w.get("value")), "max": num(w.get("max")),
            "text": F._clean_text(w.get("text"), 2000) if isinstance(w.get("text"), str) else None,
            "fmt": w.get("fmt") if w.get("fmt") in page_widgets.FMTS else None,
            "as_of": w.get("as_of") if isinstance(w.get("as_of"), str) and re.fullmatch(r"[0-9T:\-Z+.]{10,32}", w["as_of"]) else None,
            "error": w.get("error") if w.get("error") in page_widgets.ERRORS else None,
            **({"data": page_widgets.clean_remote_data(w["kind"], w.get("data")) if w.get("error") not in page_widgets.ERRORS else None}
               if w.get("kind") in page_widgets.KINDS2 else {})}


def _w_formula_check(uid, nid, b):
    """Formel-Editor fuer eine Datenbank einer anderen Instanz: deren Server prueft und rechnet die Vorschau."""
    p, rid = _peer_for(uid, nid, "formula1")
    body = {k: b[k] for k in ("expr", "pid", "names") if k in b}
    status, _, d = _call(uid, p, "POST", f"/fed/v2/notes/{rid}/formula/check", "", body)
    if status != 200 or not isinstance(d, dict):
        return F._pass_err(p, status, d)
    err = d.get("error") if isinstance(d.get("error"), dict) else None
    out = {"ok": d.get("ok") is True, "rtype": d.get("rtype") if d.get("rtype") in ("number", "text", "checkbox", "date", "list") else None,
           "expr": d["expr"][:2000] if isinstance(d.get("expr"), str) else None,
           "display": d["display"][:4000] if isinstance(d.get("display"), str) else None,
           "error": {"pos": err.get("pos") if isinstance(err.get("pos"), int) else 0, "msg": F._clean_text(err.get("msg"), 300),
                     "code": err.get("code") if isinstance(err.get("code"), str) else "syntax"} if err else None,
           "preview": []}
    for x in (d.get("preview") or [])[:5] if isinstance(d.get("preview"), list) else []:
        if isinstance(x, dict) and F._ok_rid(x.get("id")):
            v = x.get("value")
            out["preview"].append({"id": F.ref(p["id"], x["id"]), "title": F._clean_text(x.get("title"), 500),
                                   "value": v if v is None or isinstance(v, (bool, int, float, str, list)) else None,
                                   "error": x.get("error") if isinstance(x.get("error"), str) else None})
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


def _w_widgets(uid, nid, tz):
    """Widgets einer Seite einer anderen Instanz (Heim): der Eigentuemer rechnet mit den Rechten der Person."""
    pid, rid = F.unref(nid)
    with _db() as c:
        p = F._home_peer(c, pid)
    if not _caps(p, "widgets1"):
        return JSONResponse({"widgets": {}, "unsupported": True}, headers={"Cache-Control": "no-store"})
    q = "tz=" + quote(tz, safe="") if tz and page_widgets.tz_ok(tz) else ""
    status, _, d = _call(uid, p, "GET", f"/fed/v2/notes/{rid}/widgets", q)
    if status != 200 or not isinstance(d, dict) or not isinstance(d.get("widgets"), dict):
        return F._pass_err(p, status, d)
    out, w2 = {}, _caps(p, "widgets2")
    for k, w in list(d["widgets"].items())[:page_widgets.WIDGETS_MAX]:
        x = _w_widget_in(w) if isinstance(k, str) and _WBID.fullmatch(k) else None
        if x and (w2 or x.get("kind") not in page_widgets.KINDS2):
            out[k] = x
    extra = {} if w2 else {"unsupported2": True}     # 2.5 Paket 4: Etappe-2-Arten nur auf dem Ursprungsserver
    return JSONResponse({"widgets": out, **extra}, headers={"Cache-Control": "no-store"})


def _w_habit(uid, nid, bid, b, tz):
    """Gewohnheit auf einer Seite einer anderen Instanz abhaken (2.5 Paket 4, widgets2): der Eigentuemer prueft und schreibt."""
    pid, rid = F.unref(nid)
    with _db() as c:
        p = F._home_peer(c, pid)
    if not _caps(p, "widgets2"):
        return _bad(t("Nur auf dem Ursprungsserver möglich."), 409)
    q = "tz=" + quote(tz, safe="") if tz and page_widgets.tz_ok(tz) else ""
    body = {"pid": b.get("pid") if isinstance(b.get("pid"), str) else "", "day": b.get("day") if isinstance(b.get("day"), str) else "",
            "on": b.get("on") is True}
    status, _, d = _call(uid, p, "POST", f"/fed/v2/notes/{rid}/widgets/{bid}/habit", q, body)
    if status != 200 or not isinstance(d, dict) or not isinstance(d.get("widget"), dict):
        return F._pass_err(p, status, d)
    return JSONResponse({"widget": _w_widget_in(d["widget"])}, headers={"Cache-Control": "no-store"})


def _w_chart(uid, nid, params):
    """Diagramm-Ansicht einer Datenbank einer anderen Instanz (widgets2)."""
    pid, rid = F.unref(nid)
    with _db() as c:
        p = F._home_peer(c, pid)
    if not _caps(p, "widgets2"):
        return JSONResponse({"chart": None, "error": "unsupported", "view": None}, headers={"Cache-Control": "no-store"})
    qs = {}
    if params.get("tz") and page_widgets.tz_ok(params["tz"]):
        qs["tz"] = params["tz"]
    if isinstance(params.get("view"), str) and re.fullmatch(r"[a-z0-9_]{1,24}", params["view"]):
        qs["view"] = params["view"]
    status, _, d = _call(uid, p, "GET", f"/fed/v2/notes/{rid}/chart", urlencode(qs))
    if status != 200 or not isinstance(d, dict):
        return F._pass_err(p, status, d)
    return JSONResponse({"chart": page_widgets.clean_remote_data("chart", d.get("chart")) if d.get("chart") else None,
                         "error": d.get("error") if d.get("error") in page_widgets.ERRORS else None,
                         "view": d.get("view") if isinstance(d.get("view"), str) and re.fullmatch(r"[a-z0-9_]{1,24}", d["view"]) else None},
                        headers={"Cache-Control": "no-store"})


def _w_versions(uid, nid):
    p, rid = _peer_for(uid, nid, "versions")
    status, _, d = _call(uid, p, "GET", f"/fed/v2/notes/{rid}/versions")
    if status != 200 or not isinstance(d, list):
        return F._pass_err(p, status, d)
    with _db() as c:
        d = F._in_persons(c, d, p, uid)
    out = []
    for v in d[:500]:
        if isinstance(v, dict) and F._ok_rid(v.get("id")):
            out.append({"id": v["id"], "title": F._clean_text(v.get("title"), 500), "created": F._num(v.get("created")),
                        "size": F._num(v.get("size")), "author": v.get("author") if isinstance(v.get("author"), dict) else None,
                        "by": v.get("by") if v.get("by") in ("me", "other", "unknown") else "unknown"})
    return F._json(200, out)


def _w_version(uid, nid, vid, restore):
    p, rid = _peer_for(uid, nid, "versions")
    status, _, d = _call(uid, p, "POST" if restore else "GET", f"/fed/v2/notes/{rid}/versions/{vid}" + ("/restore" if restore else ""))
    if status != 200 or not isinstance(d, dict):
        return F._pass_err(p, status, d)
    if restore:
        return F._json(200, {"ok": True, "updated": F._num(d.get("updated")), "rev": d.get("rev") if isinstance(d.get("rev"), int) else None})
    return F._json(200, {"title": F._clean_text(d.get("title"), 500), "created": F._num(d.get("created")),
                         "text": d.get("text")[:5000] if isinstance(d.get("text"), str) else ""})


def _w_export(uid, nid, sub):
    p, rid = _peer_for(uid, nid, "export")
    status, rh, data = _call(uid, p, "GET", f"/fed/v2/notes/{rid}/export", "sub=1" if sub else "", limit=EXPORT_MAX, raw=True)
    if status != 200:
        try:
            d = json.loads(data or b"{}")
        except ValueError:
            d = {}
        return F._pass_err(p, status, d if isinstance(d, dict) else {})
    mime = (rh.get("content-type") or "").split(";")[0].strip().lower()
    disp = rh.get("content-disposition") or ""
    m = re.search(r"filename\*=UTF-8''([^;\s]+)", disp)
    name = F._clean_text(unquote(m.group(1)) if m else "", 120) or ("export.zip" if mime == "application/zip" else "export.md")
    name = name.replace("/", "_").replace("\\", "_")
    if mime == "application/zip":
        return Response(data, media_type="application/zip", headers={"Content-Disposition": _disposition(name), "Cache-Control": "no-store",
                                                                     "X-Content-Type-Options": "nosniff"})
    return Response(data, media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": _disposition(name),
                                                                              "X-Content-Type-Options": "nosniff"})


_CSV_KEYS = ("view", "all", "sep", "safe", "tz", "today", "q", "filters", "sorts", "group", "cols")


def _w_csv(uid, nid, params):
    p, rid = _peer_for(uid, nid, "csv")
    query = "&".join(f"{k}={quote(str(params[k]), safe='')}" for k in _CSV_KEYS if k in params)
    if len(query) > 8000:
        return _bad("bad parameter: zu lang")
    status, rh, data = _call(uid, p, "GET", f"/fed/v2/notes/{rid}/export.csv", query, limit=EXPORT_MAX, raw=True)
    if status != 200:
        try:
            d = json.loads(data or b"{}")
        except ValueError:
            d = {}
        return F._pass_err(p, status, d if isinstance(d, dict) else {})
    m = re.search(r"filename\*=UTF-8''([^;\s]+)", rh.get("content-disposition") or "")
    name = (F._clean_text(unquote(m.group(1)) if m else "", 120) or "export.csv").replace("/", "_").replace("\\", "_")
    if not name.lower().endswith(".csv"):
        name += ".csv"
    return Response(data, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": _disposition(name), "Cache-Control": "no-store",
                                                                        "X-Content-Type-Options": "nosniff"})


def _w_move(uid, nid, b):
    p, rid = _peer_for(uid, nid, "subpages")
    par = b.get("parent_id")
    try:
        par = int(par) if par is not None else None
    except (TypeError, ValueError, OverflowError):
        return _bad(t("ungültige Angabe"))
    if par is None or par >= 0 or F.unref(par)[0] != p["id"]:
        return _bad(t("Seiten anderer Saga-Instanzen lassen sich nur innerhalb des geteilten Bereichs verschieben."), 403)
    try:
        index = max(0, int(b.get("index", 0)))
    except (TypeError, ValueError):
        return _bad(t("ungültige Position"))
    status, _, d = _call(uid, p, "POST", f"/fed/v2/notes/{rid}/move", body={"parent_id": F.unref(par)[1], "index": index})
    if status != 200 or not isinstance(d, dict):
        return F._pass_err(p, status, d)
    return F._json(200, {"ok": True, "parent_id": par, "index": d.get("index") if isinstance(d.get("index"), int) else index})


def create(uid, b, origin=None):
    """pages.FED_CREATE: Unterseite/Zeile in einer Seite einer anderen Instanz (POST /api/notes mit negativer parent_id)."""
    try:
        nid = int(b.get("parent_id"))
        p, rid = _peer_for(uid, nid, "subpages")
    except (TypeError, ValueError, OverflowError):
        return _bad(t("ungültige Angabe"))
    except F._Fail as f:
        return f.resp
    if nid >= 0:
        return _bad(t("ungültige Angabe"))
    out = {k: b[k] for k in ("title", "tags", "kind") if k in b}
    if "props" in b:
        out["props"] = F.props_out(p["id"], b["props"])
    if isinstance(b.get("icon"), str) and b["icon"]:
        out["icon"] = F._pic_out(b["icon"], p["id"])
    try:
        status, _, d = _call(uid, p, "POST", f"/fed/v2/notes/{rid}/children", body=out)
    except F._Fail as f:
        return f.resp
    if status != 200 or not isinstance(d, dict) or not F._ok_rid(d.get("id")):
        return F._pass_err(p, status, d if isinstance(d, dict) else {})
    return F._json(200, {"id": F.ref(p["id"], d["id"]), "updated": F._num(d.get("updated"))})


def _w_duplicate(uid, nid, b):
    """Schaltflaeche mit Vorlage auf einer Seite einer anderen Instanz (POST /api/notes/{-vorlage}/duplicate): Vorlage,
    Ziel-Datenbank (parent_id) und Seite mit der Schaltflaeche (via) muessen auf DERSELBEN Instanz liegen - dort legt
    deren Server den Eintrag an und prueft die Schaltflaeche (Signatur des Eigentuemers)."""
    p, rid = _peer_for(uid, nid, "tplbtn" if b.get("via") is not None else "rowtpl")
    ids = {}
    for k in ("parent_id", "via"):
        if k == "via" and b.get("via") is None:       # fix27 E1: Eintrags-Vorlage ohne Schaltflaeche
            continue
        try:
            v = int(b.get(k))
        except (TypeError, ValueError, OverflowError):
            return _bad("not found", 404)
        if v >= 0 or F.unref(v)[0] != p["id"]:
            return _bad("not found", 404)
        ids[k] = F.unref(v)[1]
    if isinstance(b.get("title"), str):
        ids["title"] = b["title"][:2000]
    status, _, d = _call(uid, p, "POST", f"/fed/v2/notes/{rid}/duplicate", body=ids)
    if status != 200 or not isinstance(d, dict) or not F._ok_rid(d.get("id")):
        return F._pass_err(p, status, d if isinstance(d, dict) else {})
    out = {"id": F.ref(p["id"], d["id"]), "updated": F._num(d.get("updated"))}
    if isinstance(d.get("fresh"), str) and re.fullmatch(r"[A-Za-z0-9_-]{8,64}", d["fresh"]):     # fix27 E4
        out.update(fresh=d["fresh"], placeholder=F._clean_text(d.get("placeholder"), 500))
    return F._json(200, out)


def _w_discard(uid, nid, b):
    """fix27 E4: neuen, unveraenderten Eintrag auf der anderen Instanz verwerfen (dort prueft deren Server)."""
    p, rid = _peer_for(uid, nid, "rowtpl")
    tok = b.get("fresh") if isinstance(b, dict) and isinstance(b.get("fresh"), str) else ""
    status, _, d = _call(uid, p, "POST", f"/fed/v2/notes/{rid}/discard", body={"fresh": tok[:64]})
    d = d if isinstance(d, dict) else {}
    if status == 200:
        return F._json(200, {"deleted": True})
    if status == 409:
        return F._json(409, {"error": F._clean_text(d.get("error"), 300) or t("Der Eintrag wurde geändert und bleibt erhalten."),
                             "deleted": False, "reason": d.get("reason") if d.get("reason") in ("changed", "expired") else "changed"})
    return F._pass_err(p, status, d)


def upload(uid, nid, name, data, image):
    """files.FED_UPLOAD: Bild/Datei in eine Seite einer anderen Instanz (liegt beim Eigentuemer)."""
    try:
        p, rid = _peer_for(uid, nid, "upload")
        status, _, raw = _call(uid, p, "POST", f"/fed/v2/notes/{rid}/files", f"name={quote(name, safe='')}&image={1 if image else 0}",
                               body=data, raw=True)
    except F._Fail as f:
        return JSONResponse({"success": 0, "error": json.loads(f.resp.body or b"{}").get("error") or "error"}, status_code=f.resp.status_code)
    try:
        d = json.loads(raw or b"{}")
    except ValueError:
        d = {}
    if status in (413, 415, 507) and isinstance(d, dict):          # zu gross / kein Bild / Grenze erreicht: Text durchreichen
        return JSONResponse({"success": 0, "error": F._clean_text(d.get("error"), 300) or f"HTTP {status}"}, status_code=status)
    if status != 200 or not isinstance(d, dict) or not isinstance(d.get("file"), dict):
        e = F._pass_err(p, status, d if isinstance(d, dict) else {})
        return JSONResponse({"success": 0, "error": json.loads(e.body).get("error")}, status_code=e.status_code)
    f = d["file"]
    m = re.fullmatch(r"/files/([0-9a-f]{24})", str(f.get("url") or ""))
    if not m:
        return JSONResponse({"success": 0, "error": t("Die andere Saga-Instanz ist gerade nicht erreichbar.")}, status_code=502)
    nm = F._clean_text(f.get("name"), 120) or name
    return JSONResponse({"success": 1, "file": {"url": f"/api/fed/files/{nid}/{m.group(1)}", "name": nm, "size": F._num(f.get("size")),
                                                "extension": F._clean_text(f.get("extension"), 10), "title": nm}})


def copy(uid, url, note):
    """files.FED_COPY: Bild/Datei in eine Seite einer anderen Instanz einfuegen - eigene Datei (/files/<id>): hochladen;
    Datei derselben anderen Instanz: dort kopieren."""
    try:
        p, rid = _peer_for(uid, note, "upload")
    except F._Fail as f:
        return f.resp
    m = F._FILES_OUT.fullmatch(url)
    if m:
        if F.unref(int(m.group(1)))[0] != p["id"]:
            return _bad(t("Bilder lassen sich nicht zwischen verschiedenen Saga-Servern kopieren."), 400)
        try:
            status, _, d = _call(uid, p, "POST", f"/fed/v2/notes/{rid}/files/copy", body={"url": f"/files/{m.group(2)}"})
        except F._Fail as f:
            return f.resp
        mm = re.fullmatch(r"/files/([0-9a-f]{24})", str((d or {}).get("url") or "")) if isinstance(d, dict) else None
        if status != 200 or not mm:
            return F._pass_err(p, status, d if isinstance(d, dict) else {})
        return F._json(200, {"url": f"/api/fed/files/{note}/{mm.group(1)}", "copied": bool(d.get("copied"))})
    m = re.fullmatch(r"/files/([0-9a-f]{24})", url)
    if not m:
        return _bad(t("ungültige Angabe"))
    with _db() as c:
        r = c.execute("SELECT name, uid, is_image FROM file WHERE id=?", (m.group(1),)).fetchone()
        okv = bool(r) and (r[1] == uid or files._file_visible(c, uid, r[1], m.group(1)))
    path = os.path.join(files.FILES_DIR, m.group(1))
    if not okv or not os.path.isfile(path):
        return _bad("not found", 404)
    with open(path, "rb") as fh:
        data = fh.read(FILE_MAX + 1)
    resp = upload(uid, note, r[0], data, bool(r[2]))
    if resp.status_code != 200:
        return resp
    return F._json(200, {"url": json.loads(resp.body)["file"]["url"], "copied": True})


def presence(uid, nid, editing, leave, name):
    """live.FED_PRESENCE: Anwesenheit/Abo auf einer Seite einer anderen Instanz."""
    pid, rid = F.unref(nid)
    me = {"username": uid, "name": name, "editing": editing, "me": True, "avatar": avatars.my_url(uid)}
    with _HL:
        cur = _H.setdefault((pid, rid), {})
        if leave:
            cur.pop(uid, None)
        else:
            cur[uid] = time.time() + WATCH_TTL
        if not cur:
            _H.pop((pid, rid), None)
    try:
        p, _ = _peer_for(uid, nid, "presence")
        status, _, d = _call(uid, p, "POST", f"/fed/v2/notes/{rid}/presence", body={"editing": editing, "leave": leave})
    except F._Fail as f:
        if f.resp.status_code == 404 and not leave:          # Gegenstelle ohne Anwesenheit: nur ich, ohne Live
            return JSONResponse({"users": [me], "ttl": live.PRESENCE_TTL, "live": False}, headers={"Cache-Control": "no-store"})
        return f.resp
    if leave:
        return JSONResponse({"ok": True})
    if status != 200 or not isinstance(d, dict):
        return F._pass_err(p, status, d if isinstance(d, dict) else {})
    with _db() as c:
        users = _users_in(c, p, d.get("users"), uid)
        by = F._in_person(c, d["by"], p, uid, {}) if isinstance(d.get("by"), dict) and d["by"].get("username") else None
    if not any(u.get("me") for u in users):
        users.append(me)
    for u in users:
        if u.get("me"):
            u.update(editing=editing, name=name if u.get("username") == uid else u.get("name"))
    return JSONResponse({"rev": d.get("rev") if isinstance(d.get("rev"), int) else None, "updated": F._num(d.get("updated")), "by": by,
                         "users": users, "ttl": live.PRESENCE_TTL, "live": live._S["loop"] is not None}, headers={"Cache-Control": "no-store"})


def _users_in(c, p, lst, uid):
    out, cache = [], {}
    for x in (lst or [])[:100]:
        if not isinstance(x, dict):
            continue
        u = F._in_person(c, {"name": x.get("name"), "username": x.get("username"), "me": False}, p, uid, cache)
        u["editing"] = x.get("editing") is True
        u["me"] = u.get("username") == uid
        out.append(u)
    return out


def _local_watch(pid, rid, uid):
    with _HL:
        exp = (_H.get((pid, rid)) or {}).get(uid)
    return bool(exp and exp > time.time())


def _ev_live(c, p, it):
    """Ein Live-Eintrag des Eigentuemer-Servers -> Live-Ereignis an das eigene Konto (nur mit laufendem Abo)."""
    typ = it.get("type")
    if typ == "tree":
        uids = set()
        for n in (it.get("users") or [])[:500]:
            if isinstance(n, str) and F.is_name(n):
                u = F._local_user_by_name(c, n)[0]
                if u:
                    uids.add(u)
        for g in (it.get("groups") or [])[:50]:
            if isinstance(g, str) and c.execute("SELECT 1 FROM fed_inbound WHERE peer_id=? AND target_type='group' AND target=? LIMIT 1",
                                                (p["id"], g)).fetchone():
                uids.update(notify.group_members(c, g))
        if uids:
            bus.emit("direct", {"uids": sorted(uids), "typ": "shared.changed", "data": {}})
        return
    rid, name = it.get("note"), it.get("to")
    if not F._ok_rid(rid) or not F.is_name(name):
        return
    uid = F._local_user_by_name(c, name)[0]
    if not uid or not _local_watch(p["id"], rid, uid):
        return
    r = F.ref(p["id"], rid)
    if typ == "note.changed":
        d = {"id": r, "rev": it.get("rev") if isinstance(it.get("rev"), int) else None, "updated": F._num(it.get("updated")),
             "gone": it.get("gone") is True, "by": None}
        if isinstance(it.get("by"), dict) and it["by"].get("username"):
            d["by"] = F._in_person(c, dict(it["by"], me=False), p, uid, {})
        if it.get("row") is True:
            d["row"] = True
        elif F._ok_rid(it.get("row")):
            d["row"] = F.ref(p["id"], it["row"])
        bus.emit("direct", {"uids": [uid], "typ": "note.changed", "data": d})
    elif typ == "presence":
        bus.emit("direct", {"uids": [uid], "typ": "presence", "data": {"note_id": r, "users": _users_in(c, p, it.get("users"), uid)}})
    elif typ == "comments.changed":
        cn = it.get("counts") if isinstance(it.get("counts"), dict) else {}
        blocks = {k: v for k, v in (cn.get("blocks") or {}).items() if isinstance(k, str) and len(k) <= 40 and isinstance(v, dict)} \
            if isinstance(cn.get("blocks"), dict) else {}
        bus.emit("direct", {"uids": [uid], "typ": "comments.changed",
                            "data": {"note_id": r, "counts": {"blocks": blocks, "open": F._num(cn.get("open")), "unread": F._num(cn.get("unread"))}}})


@router.post("/fed/v2/live")
async def fed_live(request: Request):
    inp, err = await F._verify(request)
    if err is not None:
        return err
    if not _caps(inp.peer, "live"):
        return F._signed(inp, 403, {"error": "live disabled"})
    items = (inp.data or {}).get("items") if isinstance(inp.data, dict) else None
    if not isinstance(items, list) or len(items) > LIVE_MAX:
        return F._signed(inp, 400, {"error": "bad items"})
    if not F._rate.allow("live:" + inp.peer["base_url"], 600):
        return F._signed(inp, 429, {"error": "too many"})

    def run():
        with _db() as c:
            for it in items:
                if isinstance(it, dict):
                    _ev_live(c, inp.peer, it)
    await run_in_threadpool(run)
    return F._signed(inp, 200, {"ok": True})


# --- Heim-Routen (vor pages/page_tools/app_api registriert: nur negative IDs) ----------------------------------------
@router.api_route("/api/notes/{nid:fedid}/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def home_other(nid: int, rest: str, request: Request):
    """Datenbank-Zeilen, Versionen, Export, Verschieben fuer Seiten anderer Instanzen; alles andere (Freigaben,
    Veroeffentlichen, Archivieren ...) gibt es dort nicht."""
    m = request.method
    mn = re.fullmatch(r"nc(?:/([1-9][0-9]{0,19}))?", rest)
    if mn and mn.group(1) and m == "GET":                # Vorschau/Download fuer <img>/Links: Sitzung reicht (wie /api/fed/files)
        uid = F._session_user(request)
        if not uid:
            return Response(b"not found", status_code=404, media_type="text/plain")
        return await run_in_threadpool(F._run, _w_nc, uid, nid, mn.group(1), dict(request.query_params))
    uid, err = F._uid_or_401(request)
    if err:
        return err
    mv = re.fullmatch(r"versions/([1-9][0-9]{0,17})(/restore)?", rest)
    if m == "GET" and rest == "rows":
        q = request.url.query or ""
        if len(q) > 8000:
            return _bad("bad parameter: zu lang")
        return await run_in_threadpool(F._run, _w_rows, uid, nid, q)
    if m == "GET" and rest == "widgets":
        return await run_in_threadpool(F._run, _w_widgets, uid, nid, request.query_params.get("tz") or "")
    mh = re.fullmatch(r"widgets/([A-Za-z0-9_-]{1,40})/habit", rest)
    if m == "POST" and mh:
        b = await _body(request) or {}
        return await run_in_threadpool(F._run, _w_habit, uid, nid, mh.group(1), b, request.query_params.get("tz") or "")
    if m == "GET" and rest == "chart":
        return await run_in_threadpool(F._run, _w_chart, uid, nid, dict(request.query_params))
    if m == "POST" and rest == "formula/check":
        b = await _body(request) or {}
        return await run_in_threadpool(F._run, _w_formula_check, uid, nid, b)
    if m == "GET" and rest == "versions":
        return await run_in_threadpool(F._run, _w_versions, uid, nid)
    if mv and ((m == "GET" and not mv.group(2)) or (m == "POST" and mv.group(2))):
        return await run_in_threadpool(F._run, _w_version, uid, nid, int(mv.group(1)), bool(mv.group(2)))
    if m == "GET" and rest == "export":
        return await run_in_threadpool(F._run, _w_export, uid, nid, request.query_params.get("sub") == "1")
    if m == "GET" and rest == "export.csv":
        return await run_in_threadpool(F._run, _w_csv, uid, nid, dict(request.query_params))
    if m == "POST" and rest == "move":
        b = await _body(request) or {}
        return await run_in_threadpool(F._run, _w_move, uid, nid, b)
    if m == "POST" and rest == "duplicate":
        b = await _body(request) or {}
        return await run_in_threadpool(F._run, _w_duplicate, uid, nid, b)
    if m == "POST" and rest == "discard":
        b = await _body(request) or {}
        return await run_in_threadpool(F._run, _w_discard, uid, nid, b)
    if mn and m == "GET":
        return await run_in_threadpool(F._run, _w_nc, uid, nid, None, dict(request.query_params))
    return _bad(t("Für Seiten anderer Saga-Instanzen nicht verfügbar."), 404)


def _w_nc(uid, nid, fileid, q):
    """Nextcloud-Dateien einer Seite einer anderen Instanz (Heim): ueber den Eigentuemer-Server, nur mit Schalter "fed"."""
    p, rid = _peer_for(uid, nid, "nc_files")
    if fileid is None:
        status, _, d = _call(uid, p, "GET", f"/fed/v2/notes/{rid}/nc")
        if status != 200 or not isinstance(d, dict) or not isinstance(d.get("items"), list):
            return _bad("not found", 404)
        items = []
        for it in d["items"][:nextcloud.BLOCKS_MAX]:
            if not isinstance(it, dict):
                continue
            x = {"fileid": str(it.get("fileid") or "")[:20], "author": str(it.get("author") or "")[:80], "ok": it.get("ok") is True}
            for k in ("name", "mime"):
                if isinstance(it.get(k), str):
                    x[k] = it[k][:250]
            for k in ("size", "mtime"):
                if isinstance(it.get(k), (int, float)) and not isinstance(it.get(k), bool):
                    x[k] = it[k]
            x["dir"], x["preview"] = it.get("dir") is True, it.get("preview") is True
            if isinstance(it.get("entries"), list):
                x["entries"] = [{"name": str(e.get("name") or "")[:250], "dir": e.get("dir") is True,
                                 "size": e.get("size") if isinstance(e.get("size"), int) else None}
                                for e in it["entries"][:20] if isinstance(e, dict)]
            items.append(x)
        return JSONResponse({"items": items, "enabled": True}, headers={"Cache-Control": "no-store"})
    qs = "a=" + quote(str(q.get("a") or "")[:80], safe="")
    try:
        w = int(q.get("w") or 0)
    except ValueError:
        w = 0
    if w:
        qs += f"&w={max(1, min(4000, w))}"
    dl = q.get("dl") == "1"
    if dl:
        qs += "&dl=1"
    status, rh, data = _call(uid, p, "GET", f"/fed/v2/notes/{rid}/nc/{fileid}", qs, limit=nextcloud.FED_MAX + 4096)
    if status != 200:
        return Response(b"not found", status_code=404, media_type="text/plain")
    h = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox", "Referrer-Policy": "no-referrer",
         "Cache-Control": "private, max-age=300"}
    if not dl:
        img = _sniff_image(data[:16])
        if not img:
            return Response(b"not found", status_code=404, media_type="text/plain")
        h["Content-Disposition"] = "inline"
        return Response(data, media_type=img, headers=h)
    disp = (rh or {}).get("content-disposition") or ""
    mm = re.search(r'filename="([A-Za-z0-9 ._()\-]{1,200})"', disp)
    h["Content-Disposition"] = f'attachment; filename="{mm.group(1) if mm else "datei"}"'
    return Response(data, media_type="application/octet-stream", headers=h)


def _home_periodic():
    now = time.time()
    with _HL:
        for k in list(_H):
            for u in [u for u, e in _H[k].items() if e <= now]:
                _H[k].pop(u, None)
            if not _H[k]:
                _H.pop(k, None)


def _periodic():
    periodic()
    _home_periodic()


pages.FED_CREATE = create
files.FED_UPLOAD = upload
files.FED_COPY = copy
live.FED_PRESENCE = presence
live.FED_WATCHERS = watchers_for_local
live.FED_FANOUT = fanout
F.PERIODIC.append(_periodic)
F.PEER_GONE_HOOKS.append(_peer_gone)
