"""Foederation fuer Personen- und Datei-Eigenschaften (2.5, Paket 1, Faehigkeit dbx1).

Eigentuemer-Server (Antwort an eine andere Instanz, dbx_out):
- Personen: lokale Konten (Handle '~…') -> '@oeffentlicher-name@hier' (bzw. pseudonymer Handle, wie Freigaben), fremde
  Personen bleiben Prinzipal; das Woerterbuch people bekommt dieselben Schluessel (Name: Anzeigename fuer andere
  Instanzen, nie der Anmeldename; ohne Profilbild-Adresse - die setzt das Heim).
- Dateien: Werte bleiben /files/<id> (das Heim schreibt sie auf seinen Proxy um; federation._file_for liefert sie, weil
  die Datei in props der Zeile steht).
- Ohne dbx1 beim Heim: Personen-/Datei-Spalten als nur-lesbare Text-Spalten ohne Werte (Original in fx_orig, kommt beim
  Speichern des Schemas zurueck - wie Formeln ohne formula1) und "unsupported": ["person", "files"]; eingehende Werte
  dieser Spalten werden verworfen (dbx_in).
Heim-Server (Antwort fuer Web/Apps hier, props_in/people_in): Prinzipale dieser Instanz -> lokale Handles, fremde
bleiben; people neu aufgebaut (lokale Namen von hier, fremde mit Herkunft); Dateien ueber /api/fed/files/<zeile>/<id>.
Beim Speichern (props_out): lokale Handles -> '@name@hier', Dateien des Proxys -> /files/<id>."""
import json
import re

from db import connect as _db
from saga import people, sharing
from saga.core import HANDLE_RE, resolve_ref, t

CAP = "dbx1"
TYPES = ("person", "files")
_PRINC = re.compile(r"@([a-z0-9][a-z0-9_-]{1,31}|~[a-z2-7]{16})@([a-z0-9.:-]{1,260})")
_FILE_IN = re.compile(r"/files/([0-9a-f]{24})")
_FILE_PROXY = re.compile(r"/api/fed/files/(-[1-9][0-9]{0,17})/([0-9a-f]{24})")
F = None                     # saga.federation (gesetzt von install(), vermeidet zyklischen Import)


def install(fed):
    global F
    F = fed


# --- Eigentuemer-Server ------------------------------------------------------------------------------------------------
def _schema_types(schema):
    return {p.get("id"): p.get("type") for p in (schema or {}).get("props") or [] if isinstance(p, dict) and p.get("type") in TYPES}


def legacy_schema(schema):
    """Fuer Heim-Server ohne dbx1: Personen/Dateien als Text-Spalte (Original in fx_orig)."""
    if not isinstance(schema, dict) or not _schema_types(schema):
        return schema
    out = []
    for p in schema.get("props") or []:
        if isinstance(p, dict) and p.get("type") in TYPES:
            q = {k: v for k, v in p.items() if k not in ("type", "multi", "notify", "max")}
            q["type"] = "text"
            q["fx_orig"] = {k: p[k] for k in ("type", "multi", "notify", "max") if k in p}
            p = q
        out.append(p)
    return dict(schema, props=out)


def _out_ref(c, peer, ref, cache):
    """Kennung hier (Handle/Prinzipal) -> Kennung fuer die andere Instanz ('@name@hier' bzw. Prinzipal) oder None."""
    if ref in cache:
        return cache[ref]
    out = None
    if isinstance(ref, str) and _PRINC.fullmatch(ref):
        out = ref
    elif isinstance(ref, str) and HANDLE_RE.fullmatch(ref):
        u = resolve_ref(c, ref)
        pub = F.fed_name(c, u, peer) if u else None
        out = f"@{pub}@{F.SELF_HOST}" if pub else None
    cache[ref] = out
    return out


def _out_people(c, peer, pm, cache):
    out = {}
    for ref, e in (pm or {}).items():
        r = _out_ref(c, peer, ref, cache)
        if not r or not isinstance(e, dict):
            continue
        name = e.get("name")
        if HANDLE_RE.fullmatch(ref):                  # lokales Konto: Name fuer andere Instanzen (wie _ext_persons)
            u = resolve_ref(c, ref)
            name = (F.display_of(c, u) if u else "") or r[1:].split("@")[0]
        elif F.is_remote(ref):
            row = c.execute("SELECT display FROM fed_person WHERE principal=?", (ref,)).fetchone()
            name = (row[0] if row else "") or ref[1:].split("@")[0]
        out[r] = {"name": name or "?", "gone": bool(e.get("gone"))}
    return out


def _out_vals(c, peer, types, pv, cache):
    for pid, typ in types.items():
        v = pv.get(pid)
        if typ == "person" and isinstance(v, list):
            pv[pid] = [x for x in (_out_ref(c, peer, y, cache) for y in v) if x]
            if not pv[pid]:
                pv.pop(pid)


def _strip_vals(types, pv):
    for pid in types:
        pv.pop(pid, None)


def dbx_out(inp, resp, nid=None):
    """Antwort eines lokalen Endpunkts (GET Seite/Zeile, /rows, PATCH, /people) fuer die anfragende Instanz umschreiben."""
    from fastapi.responses import JSONResponse
    if not isinstance(resp, JSONResponse) or resp.status_code != 200:
        return resp
    try:
        d = json.loads(resp.body)
    except ValueError:
        return resp
    if not isinstance(d, dict):
        return resp
    ok = F.feature(inp.peer, CAP)
    cache = {}
    with _db() as c:
        if isinstance(d.get("people"), list):          # GET .../people (Auswahl)
            if not ok:
                d = {"people": [], "more": False, "unsupported": list(TYPES)}
            else:
                pl = []
                for e in d["people"]:
                    r = _out_ref(c, inp.peer, e.get("ref"), cache) if isinstance(e, dict) else None
                    if r:
                        pl.append({"ref": r, "name": _out_people(c, inp.peer, {e["ref"]: e}, cache).get(r, {}).get("name") or "?"})
                d["people"] = pl
            return _resp(d, resp)
        schema = d.get("schema") if isinstance(d.get("schema"), dict) else None
        if schema is not None and d.get("kind") != "database" and "rows" not in d:
            schema = None
        if schema is None and nid is not None:          # Einzelzeile / PATCH: Schema der Eltern-Datenbank
            r = c.execute("SELECT d.schema FROM note n JOIN note d ON d.id=n.parent_id AND d.uid=n.uid WHERE n.id=? AND d.kind='database'",
                          (nid,)).fetchone()
            schema = json.loads(r[0]) if r and r[0] else None
            row_schema = True
        else:
            row_schema = False
        types = _schema_types(schema)
        if not types:
            return resp
        rows = d.get("rows") if isinstance(d.get("rows"), list) else []
        targets = [x.get("props") for x in rows if isinstance(x, dict) and isinstance(x.get("props"), dict)]
        if row_schema and isinstance(d.get("props"), dict):
            targets.append(d["props"])
        if row_schema and isinstance(d.get("values"), dict):
            targets.append(d["values"])
        for pv in targets:
            (_out_vals(c, inp.peer, types, pv, cache) if ok else _strip_vals(types, pv))
        if ok:
            if isinstance(d.get("people"), dict):
                d["people"] = _out_people(c, inp.peer, d["people"], cache)
        else:
            d.pop("people", None)
            d.pop("values", None)
            d["unsupported"] = list(TYPES)
            if not row_schema and isinstance(d.get("schema"), dict):
                d["schema"] = legacy_schema(d["schema"])
            if any(isinstance(g, dict) and isinstance(g.get("value"), list) for g in d.get("groups") or []):
                d.pop("groups", None)                  # Gruppen nach Person kennt die Gegenstelle nicht
    return _resp(d, resp)


def _resp(d, resp):
    from fastapi.responses import JSONResponse
    return JSONResponse(d, status_code=200, headers={k: v for k, v in resp.headers.items() if k.lower() == "cache-control"})


def dbx_in(inp, b, db_id=None, row_id=None):
    """Eingehende Werte einer anderen Instanz (PATCH Zeile, neue Zeile): ohne dbx1 Personen-/Datei-Werte verwerfen."""
    if F.feature(inp.peer, CAP) or not isinstance(b.get("props"), dict):
        return b
    with _db() as c:
        if db_id is None and row_id is not None:
            r = c.execute("SELECT parent_id FROM note WHERE id=?", (row_id,)).fetchone()
            db_id = r[0] if r else None
        r = c.execute("SELECT schema FROM note WHERE id=? AND kind='database'", (db_id,)).fetchone() if db_id else None
    types = _schema_types(json.loads(r[0]) if r and r[0] else None)
    if types:
        b = dict(b, props={k: v for k, v in b["props"].items() if k not in types})
    return b


# --- Heim-Server -------------------------------------------------------------------------------------------------------
def _is_person_list(v):
    return isinstance(v, list) and v and all(isinstance(x, str) and _PRINC.fullmatch(x) for x in v)


def _is_file_list(v):
    return isinstance(v, list) and v and all(isinstance(x, dict) and isinstance(x.get("url"), str) for x in v)


def _in_ref(c, pr):
    """Prinzipal aus der Antwort der anderen Instanz -> Kennung hier (eigenes Konto: Handle; fremd: Prinzipal) oder None."""
    m = _PRINC.fullmatch(pr)
    if not m:
        return None
    if m.group(2) == F.SELF_HOST:
        u, _ = F._local_user_by_name(c, m.group(1))
        return people.to_ref(c, u) if u else None
    return people.canon_remote(c, pr)


def props_in(c, props, row_ref=None):
    """Personen- und Datei-Werte einer Zeile der anderen Instanz fuer die Anzeige hier (nach federation.props_in)."""
    if not isinstance(props, dict):
        return props
    for k, v in list(props.items()):
        if _is_person_list(v):
            props[k] = [x for x in dict.fromkeys(_in_ref(c, y) for y in v[:people.VALUES_MAX]) if x]
        elif _is_file_list(v):
            out = []
            for x in v[:20]:
                m = _FILE_IN.fullmatch(x["url"])
                if not m or row_ref is None:
                    continue
                f = {"url": f"/api/fed/files/{row_ref}/{m.group(1)}", "name": F._clean_text(x.get("name"), 120)}
                if isinstance(x.get("size"), int) and not isinstance(x.get("size"), bool):
                    f["size"] = x["size"]
                if isinstance(x.get("mime"), str) and len(x["mime"]) <= 120:
                    f["mime"] = x["mime"]
                f["image"] = x.get("image") is True
                out.append(f)
            if out:
                props[k] = out
            else:
                props.pop(k)
    return props


def people_in(c, peer, uid, pm, rows_props):
    """Woerterbuch people der anderen Instanz -> people hier (Schluessel wie die umgeschriebenen Werte)."""
    if not isinstance(pm, dict):
        pm = {}
    want = {x for pv in rows_props if isinstance(pv, dict) for v in pv.values() if isinstance(v, list)
            for x in v if isinstance(x, str) and (HANDLE_RE.fullmatch(x) or _PRINC.fullmatch(x))}
    if not want:
        return {}
    back = {}
    for pr, e in list(pm.items())[:2000]:
        if isinstance(pr, str) and isinstance(e, dict):
            r = _in_ref(c, pr)
            if r:
                back[r] = e
    canon = {x: people.to_canon(c, x) for x in want}
    out = people.label_map(c, [cv for cv in canon.values() if cv], uid)
    for x in want:
        e = back.get(x) or {}
        if F.is_remote(x):
            nm = F.remote_name(e.get("name")) if isinstance(e.get("name"), str) else ""
            out[x] = dict(out.get(x) or {}, name=sharing.remote_label(x, nm), remote=True, me=x == uid)
            if e.get("gone") is True:
                out[x]["gone"] = True
        elif x not in out:
            out[x] = {"name": t("Gelöschte Person"), "avatar": None, "me": False, "remote": False, "gone": True}
    return out


def props_out(pid, props):
    """Beim Speichern zurueck an die andere Instanz: lokale Handles -> '@name@hier', Prinzipale bleiben; Dateien des
    Proxys derselben Instanz -> /files/<id> (andere fallen weg - Dateien dieses Servers kennt der Eigentuemer nicht)."""
    if not isinstance(props, dict):
        return props
    out = dict(props)
    with _db() as c:
        p = None
        for k, v in props.items():
            if isinstance(v, list) and v and all(isinstance(x, str) and (HANDLE_RE.fullmatch(x) or _PRINC.fullmatch(x)) for x in v):
                res = []
                for x in v:
                    if _PRINC.fullmatch(x):
                        res.append(x)
                        continue
                    p = p or F._home_peer(c, pid)
                    u = resolve_ref(c, x)
                    pub = F.fed_name(c, u, p) if u else None
                    if pub:
                        res.append(f"@{pub}@{F.SELF_HOST}")
                out[k] = res
            elif _is_file_list(v):
                res = []
                for x in v:
                    m = _FILE_PROXY.fullmatch(x["url"])
                    if m and F.unref(int(m.group(1)))[0] == pid:
                        res.append({"url": f"/files/{m.group(2)}", "name": str(x.get("name") or "")[:120]})
                out[k] = res
    return out
