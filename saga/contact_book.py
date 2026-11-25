"""Adressbuch (2.6, Bugs-DB 2027a, Feature contacts2): Kontakte mit eigenen Feldern - "Name fuer mich", Anrede (du/Sie),
Grussform, Telefonnummern (E.164) und E-Mail-Adressen. Vertrag fuer Apps: docs/APP-API.md ("Kontakte (2.6, Bugs-DB 2027a,
contacts2)"). Modell und Weichen in saga/contacts.py (Tabellen contact / contact_addr, Migration 38).

Grundsaetze:
- Ein Kontakt vergibt KEINE Rechte (contacts.has() liest nur chat_contact). Saga-/Fed-Adressen (fest) entstehen nur aus
  gemeinsamen Chats; hier lassen sie sich weder anlegen noch aendern noch einzeln loeschen.
- Nur der Besitzer sieht und aendert seine Kontakte (fremde ID -> 404). form/salutation nur vom Besitzer, nie aus Chats
  abgeleitet (RT-41). own_name erscheint nur in Kontaktliste/-detail, nie in Chats.
- Nummern und Mails stehen nie im Log und nie in Fehlermeldungen (Texte uebersetzbar, ohne Eingabewerte); ein doppelter
  Wert meldet 409 nur mit contact_id.
- Das Adressbuch verlaesst den Server nie (kein fed_*-Modul liest es; Strukturtest in tests/kontakte2_api.py)."""
import re
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from db import connect as _db
from saga import bus, contacts
from saga.core import _api_guard, _bad, _body, t

router = APIRouter()

NAME_MAX = 120
SAL_MAX = 80
MANUAL_MAX = 2000                     # selbst angelegte Kontakte je Konto
KIND_MAX = 10                         # je Kontakt hoechstens so viele Nummern bzw. Mails
LIST_MAX = 200
SCAN_MAX = 5000                       # so viele Kontakte werden fuer eine Suche hoechstens durchgesehen
FORMS = ("informal", "formal")
TYPES = ("mobile", "home", "work", "other")
PRIVATE = ("e164", "email")
KV_CC = "contacts.cc"                 # user_kv: Landesvorwahl fuer nationale Nummern ('49'), fehlt = keine
RATE_READ, RATE_WRITE = 60, 30        # je Minute und Konto

E164_RE = contacts.E164_RE
CC_RE = re.compile(r"[1-9]\d{0,2}")
_SEP = re.compile(r"[\s\-/.()   ]")
_TRUNK = re.compile(r"^\+(\d{1,3})\s*\(0\)")      # '+49 (0) 170 …' -> '+49 170 …'
_CTRL = re.compile(r"[\s\x00-\x1f\x7f]")


class Bad(Exception):
    def __init__(self, msg, code=400, extra=None):
        super().__init__(msg)
        self.code, self.extra = code, extra or {}


# =====================================================================================================================
# Pruefen und Normalisieren (Fehlertexte nie mit Eingabewerten)
# =====================================================================================================================
def norm_cc(v):
    """Landesvorwahl: '49', 49, '+49' -> '49'; None/'' -> None; sonst Bad."""
    if v is None or v == "":
        return None
    if isinstance(v, bool) or not isinstance(v, (str, int)):
        raise Bad(t("Ungültige Landesvorwahl."))
    s = str(v).strip().lstrip("+")
    if not CC_RE.fullmatch(s):
        raise Bad(t("Ungültige Landesvorwahl."))
    return s


def norm_e164(v, cc=None):
    """Telefonnummer -> '+491701234567'. Leerzeichen - / . Klammern weg, '00…' -> '+…', '0…' nur mit Landesvorwahl cc."""
    if not isinstance(v, str) or not v.strip() or len(v) > 64:
        raise Bad(t("Ungültige Telefonnummer."))
    s = _SEP.sub("", _TRUNK.sub(r"+\1", v.strip()))
    if s.startswith("00"):
        s = "+" + s[2:]
    elif s.startswith("0"):
        if not cc:
            raise Bad(t("Für nationale Nummern bitte die Landesvorwahl einstellen oder die Nummer mit + beginnen."))
        s = "+" + cc + s[1:]
    if not E164_RE.fullmatch(s):
        raise Bad(t("Ungültige Telefonnummer."))
    return s


def norm_email(v):
    """E-Mail: hoechstens 254 Zeichen, genau ein '@', keine Leer-/Steuerzeichen, Domain klein."""
    if not isinstance(v, str):
        raise Bad(t("Ungültige E-Mail-Adresse."))
    s = v.strip()
    if not s or len(s) > 254 or s.count("@") != 1 or _CTRL.search(s):
        raise Bad(t("Ungültige E-Mail-Adresse."))
    local, dom = s.split("@")
    dom = dom.lower()
    if not local or not dom or dom.startswith(".") or dom.endswith(".") or ".." in dom:
        raise Bad(t("Ungültige E-Mail-Adresse."))
    return local + "@" + dom


def _text(v, mx, too_long):
    if v is None:
        return None
    if not isinstance(v, str):
        raise Bad(t("ungültige Angabe"))
    s = re.sub(r"\s+", " ", v).strip()
    if len(s) > mx:
        raise Bad(too_long)
    return s or None


def _own_name(v):
    return _text(v, NAME_MAX, t("Der Name ist zu lang (höchstens {n} Zeichen).", n=NAME_MAX))


def _salutation(v):
    return _text(v, SAL_MAX, t("Die Grußform ist zu lang (höchstens {n} Zeichen).", n=SAL_MAX))


def _form(v):
    if v is None or v in FORMS:
        return v
    raise Bad(t("Ungültige Anrede."))


def _addrs_in(items, cc, fixed_vals):
    """Eingabe addrs -> [(kind, value, type)] (nur private, ohne Dubletten, Reihenfolge = pos). Feste Adressen in der
    Eingabe werden uebergangen, wenn sie unveraendert sind (Apps schicken die Liste aus GET zurueck); andere Saga-/Fed-
    Adressen -> 400 (entstehen nur aus gemeinsamen Chats)."""
    if not isinstance(items, list) or len(items) > 4 * KIND_MAX:
        raise Bad(t("ungültige Angabe"))
    out, seen, n = [], set(), {"e164": 0, "email": 0}
    for x in items:
        if not isinstance(x, dict):
            raise Bad(t("ungültige Angabe"))
        kind = x.get("kind")
        if kind in contacts.FIXED_KINDS:
            if x.get("value") in fixed_vals:
                continue
            raise Bad(t("Saga-Adressen entstehen nur aus gemeinsamen Chats."))
        if kind not in PRIVATE:
            raise Bad(t("ungültige Angabe"))
        typ = x.get("type")
        if typ is not None and typ not in TYPES:
            raise Bad(t("Ungültiger Adresstyp."))
        val = norm_e164(x.get("value"), cc) if kind == "e164" else norm_email(x.get("value"))
        if (kind, val) in seen:
            continue
        seen.add((kind, val))
        n[kind] += 1
        if n["e164"] > KIND_MAX:
            raise Bad(t("Höchstens {n} Telefonnummern je Kontakt.", n=KIND_MAX))
        if n["email"] > KIND_MAX:
            raise Bad(t("Höchstens {n} E-Mail-Adressen je Kontakt.", n=KIND_MAX))
        out.append((kind, val, typ))
    return out


def get_cc(c, uid):
    r = c.execute("SELECT v FROM user_kv WHERE uid=? AND k=?", (uid, KV_CC)).fetchone()
    try:
        return norm_cc(r[0]) if r else None
    except Bad:
        return None


def _cc_for(c, uid, b):
    """cc der Anfrage, sonst (fehlt oder null/"") die Konto-Einstellung."""
    return norm_cc(b.get("cc")) if b.get("cc") not in (None, "") else get_cc(c, uid)


# =====================================================================================================================
# Ansicht
# =====================================================================================================================
COLS = "c.id, c.ver, c.origin, c.own_name, c.form, c.salutation, c.created, c.updated"
_LAST = ("LEFT JOIN LATERAL (SELECT MAX(cc.last_at) AS last_at FROM contact_addr a JOIN chat_contact cc ON cc.uid=a.uid "
         "AND cc.ref=a.value WHERE a.contact_id=c.id AND a.fixed=1) x ON true")


def _rows(c, uid, ids=None, limit=None, offset=0):
    sql = f"SELECT {COLS}, x.last_at FROM contact c {_LAST} WHERE c.uid=?"
    args = [uid]
    if ids is not None:
        if not ids:
            return []
        sql += f" AND c.id IN ({','.join('?' * len(ids))})"
        args += list(ids)
    sql += " ORDER BY COALESCE(x.last_at, c.updated) DESC, c.id DESC"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        args += [limit, offset]
    return c.execute(sql, tuple(args)).fetchall()


def _views(c, uid, rows, with_live=False):
    """Zeilen aus _rows -> Kontakt-Objekte (Vertrag docs/APP-API.md)."""
    ids = [r[0] for r in rows]
    addrs = {i: [] for i in ids}
    if ids:
        for a in c.execute(f"SELECT contact_id, id, kind, value, type, fixed FROM contact_addr WHERE uid=? AND contact_id IN "
                           f"({','.join('?' * len(ids))}) ORDER BY fixed DESC, pos, id", (uid, *ids)):
            addrs[a[0]].append({"id": a[1], "kind": a[2], "value": a[3], "type": a[4], "fixed": bool(a[5])})
    refs = [a["value"] for v in addrs.values() for a in v if a["fixed"]]
    cache = {}
    if refs:
        cache = {r[0]: (r[1], r[2]) for r in c.execute(
            f"SELECT ref, name, avatar FROM chat_contact WHERE uid=? AND ref IN ({','.join('?' * len(refs))})", (uid, *refs))}
    info = contacts.describe(c, refs, cache)
    out = []
    for cid, ver, origin, own, form, sal, created, updated, last in rows:
        ad = addrs.get(cid, [])
        fx = next((a for a in ad if a["fixed"]), None)
        saga = None
        live = ""
        if fx:
            d = info.get(fx["value"]) or {"ref": fx["value"], "name": "", "sub": "", "avatar": None, "ready": False,
                                          "gone": True, "remote": fx["kind"] == "fed"}
            live = d.get("name") or ""
            saga = {"ref": fx["value"], "remote": bool(d.get("remote")), "host": d.get("host"), "sub": d.get("sub") or "",
                    "avatar": d.get("avatar"), "ready": bool(d.get("ready")), "gone": bool(d.get("gone"))}
        first = next((a["value"] for a in ad if not a["fixed"]), "")
        v = {"id": cid, "ver": ver, "origin": origin, "name": own or live or first, "own_name": own, "saga": saga,
             "form": form, "salutation": sal, "addrs": ad, "last_at": last, "created": created, "updated": updated}
        if with_live:
            v["_live"] = live
        out.append(v)
    return out


def one(c, uid, cid):
    rows = _rows(c, uid, [cid])
    return _views(c, uid, rows)[0] if rows else None


def _digits(s):
    return re.sub(r"\D", "", s or "")


def _match(v, q, qd):
    if any(q in (v[k] or "").lower() for k in ("name", "own_name", "_live")):
        return True
    if v["saga"] and q in (v["saga"]["sub"] or "").lower():
        return True
    for a in v["addrs"]:
        if a["kind"] == "email" and q in a["value"].lower():
            return True
        if a["kind"] == "e164" and qd and (qd in a["value"] or qd.lstrip("0") and qd.lstrip("0") in a["value"]):
            return True
    return False


def listing(c, uid, q="", limit=LIST_MAX, offset=0):
    """-> ({contacts, total}) zuletzt zuerst (last_at aus chat_contact, sonst updated)."""
    q = re.sub(r"\s+", " ", str(q or "")).strip().lower()[:60]
    limit = max(1, min(int(limit or LIST_MAX), LIST_MAX))
    offset = min(max(0, int(offset or 0)), SCAN_MAX * 1000)     # (Postgres OFFSET: bigint - riesige Werte sonst 500)
    if not q:
        total = c.execute("SELECT COUNT(*) FROM contact WHERE uid=?", (uid,)).fetchone()[0]
        return {"contacts": _views(c, uid, _rows(c, uid, limit=limit, offset=offset)), "total": total}
    qd = _digits(q) if len(_digits(q)) >= 3 else ""
    hits = [v for v in _views(c, uid, _rows(c, uid, limit=SCAN_MAX), with_live=True) if _match(v, q, qd)]
    for v in hits:
        v.pop("_live", None)
    return {"contacts": hits[offset:offset + limit], "total": len(hits)}


# =====================================================================================================================
# Schreiben (Advisory-Lock je Konto, contacts.lock)
# =====================================================================================================================
def _conflict(c, uid, items, cid=None):
    for kind, val, _ in items:
        other = contacts.contact_for(c, uid, kind, val)
        if other and other != cid:
            raise Bad(t("Diese Adresse gehört schon zu einem anderen Kontakt."), 409, {"contact_id": other})


def _put_addrs(c, uid, cid, items, now):
    """Private Adressen von cid durch items ersetzen; gleicher Wert behaelt seine id (Typ/Reihenfolge neu)."""
    have = {(r[1], r[2]): r[0] for r in c.execute("SELECT id, kind, value FROM contact_addr WHERE contact_id=? AND uid=? AND fixed=0",
                                                  (cid, uid))}
    keep = set()
    for pos, (kind, val, typ) in enumerate(items, 1):
        aid = have.get((kind, val))
        if aid:
            keep.add(aid)
            c.execute("UPDATE contact_addr SET type=?, pos=? WHERE id=?", (typ, pos, aid))
        else:
            c.execute("INSERT INTO contact_addr (contact_id, uid, kind, value, type, fixed, pos, created) VALUES (?,?,?,?,?,0,?,?)",
                      (cid, uid, kind, val, typ, pos, now))
    gone = [i for i in have.values() if i not in keep]
    if gone:
        c.execute(f"DELETE FROM contact_addr WHERE id IN ({','.join('?' * len(gone))})", tuple(gone))


def create(c, uid, b):
    contacts.lock(c, [uid])
    own = _own_name(b.get("own_name"))
    if not own:
        raise Bad(t("Bitte einen Namen angeben."))
    form = _form(b.get("form"))
    sal = _salutation(b.get("salutation"))
    items = _addrs_in(b.get("addrs") if "addrs" in b else [], _cc_for(c, uid, b), set())
    if not items:
        raise Bad(t("Bitte mindestens eine Telefonnummer oder E-Mail-Adresse angeben."))
    if c.execute("SELECT COUNT(*) FROM contact WHERE uid=? AND origin='manual'", (uid,)).fetchone()[0] >= MANUAL_MAX:
        raise Bad(t("Höchstens {n} eigene Kontakte je Konto.", n=MANUAL_MAX))
    _conflict(c, uid, items)
    now = time.time()
    cid = c.execute("INSERT INTO contact (uid, origin, own_name, form, salutation, created, updated, ver) VALUES (?, 'manual', ?,?,?,?,?,1)",
                    (uid, own, form, sal, now, now)).lastrowid
    _put_addrs(c, uid, cid, items, now)
    contacts.changed(c, uid, cid)
    return one(c, uid, cid), 201


def update(c, uid, cid, b):
    contacts.lock(c, [uid])
    cur = one(c, uid, cid)
    if not cur:
        raise Bad(t("Kontakt nicht gefunden."), 404)
    ver = b.get("ver")
    if isinstance(ver, bool) or not isinstance(ver, int):
        raise Bad(t("ungültige Angabe"))
    if ver != cur["ver"]:
        raise Bad(t("Der Kontakt wurde inzwischen geändert – bitte neu laden."), 409, {"contact": cur})
    manual = cur["origin"] == "manual"
    own = _own_name(b["own_name"]) if "own_name" in b else cur["own_name"]
    if manual and "own_name" in b and not own:
        raise Bad(t("Bitte einen Namen angeben."))
    form = _form(b["form"]) if "form" in b else cur["form"]
    sal = _salutation(b["salutation"]) if "salutation" in b else cur["salutation"]
    items = None
    if "addrs" in b:
        fixed = {a["value"] for a in cur["addrs"] if a["fixed"]}
        items = _addrs_in(b["addrs"], _cc_for(c, uid, b), fixed)
        if manual and not fixed and not items:
            raise Bad(t("Bitte mindestens eine Telefonnummer oder E-Mail-Adresse angeben."))
        _conflict(c, uid, items, cid)
    now = time.time()
    c.execute("UPDATE contact SET own_name=?, form=?, salutation=?, updated=?, ver=ver+1 WHERE id=? AND uid=?",
              (own, form, sal, now, cid, uid))
    if items is not None:
        _put_addrs(c, uid, cid, items, now)
    contacts.changed(c, uid, cid)
    return one(c, uid, cid), 200


def delete(c, uid, cid):
    """Kontakt loeschen; seine Saga-/Fed-Kennungen verschwinden auch aus den Chat-Vorschlaegen (chat_contact hidden, wie
    "Aus Kontakten entfernen" - ein neuer gemeinsamer Chat legt wieder einen leeren Kontakt an)."""
    contacts.lock(c, [uid])
    if not c.execute("SELECT 1 FROM contact WHERE id=? AND uid=?", (cid, uid)).fetchone():
        raise Bad(t("Kontakt nicht gefunden."), 404)
    refs = [r[0] for r in c.execute("SELECT value FROM contact_addr WHERE contact_id=? AND uid=? AND fixed=1", (cid, uid))]
    c.execute("DELETE FROM contact WHERE id=? AND uid=?", (cid, uid))
    for ref in refs:
        if c.execute("UPDATE chat_contact SET hidden=1 WHERE uid=? AND ref=? AND hidden=0", (uid, ref)).rowcount:
            bus.emit_after(c, "direct", {"uids": [uid], "typ": "chat.contacts", "data": {"ref": ref, "hidden": True}})
    contacts.changed(c, uid, cid, deleted=True)
    return {"ok": True}, 200


def set_cc(c, uid, b):
    cc = norm_cc(b.get("cc"))
    if cc:
        c.execute("INSERT OR REPLACE INTO user_kv (uid, k, v) VALUES (?, ?, ?)", (uid, KV_CC, cc))
    else:
        c.execute("DELETE FROM user_kv WHERE uid=? AND k=?", (uid, KV_CC))
    return {"cc": cc}, 200


# =====================================================================================================================
# Endpunkte
# =====================================================================================================================
_hits = {}


def _rate(kind, uid, n, per=60.0):
    now = time.time()
    k = (kind, uid)
    h = [x for x in _hits.get(k, []) if now - x < per]
    if len(h) >= n:
        _hits[k] = h
        return False
    _hits[k] = h + [now]
    if len(_hits) > 20000:
        _hits.clear()
    return True


def _no_store(d, code=200):
    return JSONResponse(d, status_code=code, headers={"Cache-Control": "no-store"})


def _err(ex):
    return _no_store(dict({"error": str(ex)}, **ex.extra), ex.code)


def _guard(request, write):
    """-> (uid, fehler)"""
    uid = _api_guard(request)
    if not uid:
        return None, _bad("unauthorized", 401)
    if not _rate("contacts.w" if write else "contacts.r", uid, RATE_WRITE if write else RATE_READ):
        return None, _no_store({"error": t("zu schnell")}, 429)
    return uid, None


def _read(fn, *a):
    with _db() as c:
        return fn(c, *a)


def _write(fn, uid, *a):
    try:
        with _db() as c, c:
            out, code = fn(c, uid, *a)
    except Bad as ex:
        return _err(ex)
    return _no_store(out, code)


@router.get("/api/contacts")
async def api_list(request: Request, q: str = "", limit: int = LIST_MAX, offset: int = 0):
    uid, err = _guard(request, False)
    if err:
        return err
    return _no_store(await run_in_threadpool(_read, listing, uid, q, limit, offset))


@router.get("/api/contacts/settings")
async def api_settings(request: Request):
    uid, err = _guard(request, False)
    if err:
        return err
    return _no_store({"cc": await run_in_threadpool(_read, get_cc, uid)})


@router.put("/api/contacts/settings")
async def api_settings_put(request: Request):
    """{cc: '49' | null} - Landesvorwahl fuer nationale Nummern ('0170 …')."""
    uid, err = _guard(request, True)
    if err:
        return err
    b = await _body(request)
    if b is None:
        return _bad(t("ungültige Angabe"))
    return await run_in_threadpool(_write, set_cc, uid, b)


@router.post("/api/contacts")
async def api_create(request: Request):
    """{own_name, form?, salutation?, addrs: [{kind: e164|email, value, type?}], cc?} -> 201 Kontakt"""
    uid, err = _guard(request, True)
    if err:
        return err
    b = await _body(request)
    if b is None:
        return _bad(t("ungültige Angabe"))
    return await run_in_threadpool(_write, create, uid, b)


@router.get("/api/contacts/{cid:int}")
async def api_get(cid: int, request: Request):
    uid, err = _guard(request, False)
    if err:
        return err
    v = await run_in_threadpool(_read, one, uid, cid)
    return _no_store(v) if v else _no_store({"error": t("Kontakt nicht gefunden.")}, 404)


@router.patch("/api/contacts/{cid:int}")
async def api_patch(cid: int, request: Request):
    """{own_name?, form?, salutation?, addrs?, cc?, ver} -> 200 Kontakt | 409 {error, contact} bei veraltetem ver"""
    uid, err = _guard(request, True)
    if err:
        return err
    b = await _body(request)
    if b is None:
        return _bad(t("ungültige Angabe"))
    return await run_in_threadpool(_write, update, uid, cid, b)


@router.delete("/api/contacts/{cid:int}")
async def api_delete(cid: int, request: Request):
    uid, err = _guard(request, True)
    if err:
        return err
    return await run_in_threadpool(_write, delete, uid, cid)
