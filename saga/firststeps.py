"""Erste Schritte (2.6, Bugs-DB 2031): beim ersten Anmelden eine freundliche Frage, ob man eine kurze Einfuehrung moechte.
Ja -> Saga legt eine Seite "Erste Schritte" mit einer Haekchenliste an und hakt erledigte Punkte selbst ab; Nein -> es wird
nichts angelegt. Spaeter in den Einstellungen (Hilfe) wieder abrufbar, die Seite ist jederzeit loeschbar, der Admin kann
das Angebot abschalten (Admin -> Allgemein, setting "first_steps"). Ganz ohne KI.

Zustand je Konto: user_kv "first_steps" = {"state": "yes"|"no", "page": id, "texts": {key: Text der Zeile}, "ticked": [keys]}.
Die Frage kommt nur, solange es keinen Zustand gibt UND das Konto noch keine eigenen Seiten hat (bestehende Konten mit
Inhalten fragt Saga nach dem Update nicht).

Punkte (Reihenfolge = Liste): avatar (Profilbild), chat (Chats einrichten + Wiederherstellungsschluessel - nur wenn Chats an
sind; die Einrichtung verlangt die letzten Zeichen des Schluessels), app (App gekoppelt = Geraete-Anmeldung), page (erste
eigene Seite ausser dieser), task (erste Aufgabe). Abgehakt wird beim Abruf von GET /api/first-steps (Web: Heute und Seiten,
Apps: beim Start) - nur neu Erledigtes, ein Punkt nie zweimal (wer selbst wieder abhakt, behaelt das). Gefunden wird die
Zeile ueber ihren gespeicherten Text; hat man ihn geaendert, bleibt sie, wie sie ist.

  GET  /api/first-steps                 -> {enabled, ask, state, page, items: [{key, done}]}
  POST /api/first-steps {answer: "yes"|"no"} -> {ok, page?}   (yes legt die Seite an bzw. neu an, wenn sie fehlt)

Tests: tests/erste_schritte.py, tests/browser_leere_zustaende.js
"""
import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import auth
from db import connect as _db
from saga.core import _api_guard, _bad, _body, t

router = APIRouter()
KEY = "first_steps"
KEYS = ("avatar", "chat", "app", "page", "task")


def enabled():
    return bool(auth.settings().get("first_steps", True))


def _chat_on():
    return bool(auth.settings().get("chat_enabled", True))


def keys():
    return [k for k in KEYS if k != "chat" or _chat_on()]


def _texts():
    """Zeilen der Liste in der Sprache der Anfrage (gespeichert, damit das Abhaken sie wiederfindet)."""
    return {
        "avatar": t("Profilbild hochladen (Benutzermenü › Einstellungen › Profil & Sprache)"),
        "chat": t("Chats einrichten und den Wiederherstellungsschlüssel sicher aufbewahren (Seitenleiste › Chats)"),
        "app": t("Eine App koppeln – Handy, Tablet oder Desktop (Einstellungen › Apps & Geräte)"),
        "page": t("Eine eigene Seite anlegen (Heute › Neue Seite)"),
        "task": t("Eine erste Aufgabe notieren (Heute › Aufgabe)"),
    }


def _load(c, uid):
    r = c.execute("SELECT v FROM user_kv WHERE uid=? AND k=?", (uid, KEY)).fetchone()
    try:
        v = json.loads(r[0]) if r and r[0] else None
    except (TypeError, ValueError):
        v = None
    return v if isinstance(v, dict) else None


def _save(c, uid, v):
    c.execute("INSERT OR REPLACE INTO user_kv (uid, k, v) VALUES (?,?,?)", (uid, KEY, json.dumps(v, ensure_ascii=False)))


def _page_ok(c, uid, nid):
    if not isinstance(nid, int) or isinstance(nid, bool):
        return False
    return bool(c.execute("SELECT 1 FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, uid)).fetchone())


def _own_pages(c, uid, skip=None):
    """Gibt es eigene Seiten/Datenbanken? (keine Eintraege in Datenbanken, keine Vorlagen, nicht im Papierkorb), ohne skip und
    dessen Unterseiten."""
    q = ("SELECT 1 FROM note n WHERE n.uid=? AND n.deleted_at IS NULL AND n.is_template=0 AND n.kind IN ('page','database') "
         "AND NOT EXISTS (SELECT 1 FROM note d WHERE d.id=n.parent_id AND d.kind='database')")
    args = [uid]
    if skip:
        q += " AND n.id<>? AND (n.parent_id IS NULL OR n.parent_id<>?)"
        args += [skip, skip]
    return c.execute(q + " LIMIT 1", tuple(args)).fetchone() is not None   # nur ob es eine gibt (Pruefer 1: kein COUNT)


def done_keys(c, uid, page=None):
    """Welche Punkte hat das Konto erledigt? (nur Daten des Kontos, keine Inhalte)"""
    from saga import avatars
    out = set()
    try:
        if avatars.has_image(uid):                   # nur ein vorhandenes Bild zaehlt, nicht ein noch zu holendes (SSO)
            out.add("avatar")
    except Exception:  # noqa: BLE001 - Profilbild-Quelle nicht erreichbar: dann eben nicht abgehakt
        pass
    if _chat_on() and c.execute("SELECT 1 FROM chat_identity WHERE uid=?", (uid,)).fetchone():
        out.add("chat")
    if c.execute("SELECT 1 FROM device_login WHERE uid=? LIMIT 1", (uid,)).fetchone():
        out.add("app")
    if _own_pages(c, uid, page):
        out.add("page")
    if c.execute("SELECT 1 FROM task WHERE uid=? LIMIT 1", (uid,)).fetchone():
        out.add("task")
    return out


def _markdown(texts, ks):
    return "\n".join([
        t("Willkommen bei Saga! Hier sind ein paar kleine Schritte für den Anfang. Was du erledigt hast, hakt Saga selbst ab."),
        "",
        *[f"- [ ] {texts[k]}" for k in ks],
        "",
        t("Du kannst diese Seite jederzeit löschen. Wiederfinden: Einstellungen › Hilfe › Erste Schritte."),
    ])


def _tok(uid):
    from saga import apitok
    return apitok.Tok(0, uid, "", "", (), None, None)


def _lock(c, uid):
    """Ein Ablauf je Konto (Pruefer 2): gleichzeitiges "Ja" aus Web und App legt nur eine Seite an, ein aelteres Abhaken
    ueberschreibt nicht den Stand einer neu angelegten Seite."""
    c.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (f"first-steps:{uid}",))


def create_page(uid):
    """Seite "Erste Schritte" anlegen -> id (in user_kv gemerkt)."""
    from saga import api_v1
    texts, ks = _texts(), keys()
    _, d = api_v1.page_create(_tok(uid), {"title": t("Erste Schritte"), "icon": "👋", "markdown": _markdown(texts, ks)})
    nid = d["id"]
    with _db() as c, c:
        v = _load(c, uid) or {}
        v.update(state="yes", page=nid, texts={k: texts[k] for k in ks}, ticked=[])
        _save(c, uid, v)
    return nid


def sync(uid):
    """Neu Erledigtes auf der Seite abhaken. -> Liste der Punkte [{key, done}] (auch ohne Seite)."""
    with _db() as lk, lk:
        _lock(lk, uid)
        return _sync(uid)


def _sync(uid):
    from saga import api_v1, blockedit
    with _db() as c:
        v = _load(c, uid) or {}
        page = v.get("page") if _page_ok(c, uid, v.get("page")) else None
        done = done_keys(c, uid, page)
    items = [{"key": k, "done": k in done} for k in keys()]
    texts, ticked = v.get("texts") or {}, set(v.get("ticked") or [])
    todo = [k for k in keys() if k in done and k not in ticked and isinstance(texts.get(k), str)]
    if page is None or not todo or v.get("state") != "yes":
        return items, page

    def fn(blocks):
        n = 0
        for k in todo:
            try:
                r = blockedit.set_checkbox(blocks, True, None, None, texts[k])
                n += 1 if r.get("changed") else 0
            except blockedit.EditError:             # Zeile geaendert oder geloescht: so lassen (gilt als erledigt)
                pass
        return {"changed": n} if n else {"unchanged": True, "changed": 0}
    try:
        api_v1.blocks_edit(_tok(uid), page, fn)
    except api_v1.Fail as f:                         # 409 gerade bearbeitet, 423 gesperrt, 404 eben geloescht ...:
        if f.status != 409:                          # nichts merken - beim naechsten Abruf erneut (Pruefer 4)
            print(f"first_steps: Abhaken nicht moeglich ({f.status})", flush=True)
        return items, page
    with _db() as c, c:
        cur = _load(c, uid) or {}
        if cur.get("page") == page:                  # inzwischen neu angelegt: nicht in den neuen Stand schreiben
            cur["ticked"] = sorted(set(cur.get("ticked") or []) | set(todo))
            _save(c, uid, cur)
    return items, page


def state(uid):
    with _db() as c:
        v = _load(c, uid)
        own = _own_pages(c, uid) if v is None else True   # nur solange noch nie geantwortet (Pruefer 1)
    on = enabled()
    out = {"enabled": on, "state": (v or {}).get("state"), "ask": bool(on and v is None and not own), "page": None, "items": []}
    if on:
        out["items"], out["page"] = sync(uid)
    return out


@router.get("/api/first-steps")
async def api_first_steps(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    return JSONResponse(await asyncio.to_thread(state, uid), headers={"Cache-Control": "no-store"})


@router.post("/api/first-steps")
async def api_first_steps_answer(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    ans = b.get("answer") if isinstance(b, dict) else None
    if ans not in ("yes", "no"):
        return _bad(t("ungültige Angabe"))
    if not enabled():
        return _bad(t("Die Einführung ist auf diesem Server abgeschaltet."), 403)

    def run():
        if ans == "no":
            with _db() as c, c:
                v = _load(c, uid) or {}
                v["state"] = v.get("state") or "no"       # wer schon eine Seite hat, behaelt sie
                _save(c, uid, v)
            return {"ok": True}
        with _db() as lk, lk:
            _lock(lk, uid)
            with _db() as c:
                v = _load(c, uid) or {}
                page = v.get("page") if _page_ok(c, uid, v.get("page")) else None
            if page is None:
                page = create_page(uid)
                _sync(uid)
        return {"ok": True, "page": page}
    return JSONResponse(await asyncio.to_thread(run), headers={"Cache-Control": "no-store"})
