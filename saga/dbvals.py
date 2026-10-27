"""Personen- und Datei-Eigenschaften in Lesepfaden (2.5, Paket 1 Etappen 2/3) - eine Stelle fuer alle Ausgaben.

- out_rows(c, owner, schema, rows, viewer) -> Zusatzfelder fuer die Antwort ({"people": {...}} bzw. {}): Personen als
  Kennung des Betrachters (saga.people.to_ref, nie der Anmeldename), Dateien mit Groesse/MIME/Bild aus der Tabelle file
  (nur Dateien des Eigentuemers; fehlende fallen weg). Fuer /rows, Einzelzeile, PATCH-Antwort, Foederation.
- text_rows(c, schema, rows): Personen -> Anzeigenamen, Dateien -> Namen (Formeln, Widgets, KI-Kontext).
- proptext: Ausgabe "person"/"files" fuer CSV/Markdown (Personen: Anzeigename; Dateien: Name).
- Mitteilung db.assigned: neue Personen in einer Zeile (bus "row.assigned" -> saga.notify, gebuendelt je Zeile).
- Kopieren (Vorlagen/Duplizieren): copy_files() kopiert Dateien eines anderen Eigentuemers (wie files.file_copy).
Personen-Werte im Speicher sind nach out_rows Kennungen des Betrachters, vorher kanonisch - immer erst NACH dbcalc.fill
und dbquery aufrufen (Filter/Sortierung arbeiten mit kanonischen Werten bzw. Namen, siehe saga.dbquery)."""
import json

from saga import bus, people, proptext
from saga.core import t
from saga.notes import _FILE_URL
from saga.sharing import names


def file_pids(schema):
    return [p["id"] for p in (schema or {}).get("props") or [] if isinstance(p, dict) and p.get("type") == "files" and p.get("id")]


def has_any(schema):
    return bool(people.person_pids(schema) or file_pids(schema))


def _file_meta(c, owner, fids):
    if not fids:
        return {}
    out = {}
    fids = sorted(fids)
    for k in range(0, len(fids), 500):
        part = fids[k:k + 500]
        for r in c.execute(f"SELECT id, name, mime, size, is_image FROM file WHERE uid=? AND id IN ({','.join('?' * len(part))})",
                           (owner, *part)).fetchall():
            out[r[0]] = {"name": r[1], "mime": r[2], "size": r[3], "image": bool(r[4])}
    return out


def files_out(c, owner, schema, rows):
    """Datei-Werte vervollstaendigen: [{url, name, size, mime, image}] - nur existierende Dateien des Eigentuemers."""
    pids = file_pids(schema)
    if not pids or not rows:
        return
    want = set()
    for r in rows:
        pv = r.get("props") if isinstance(r.get("props"), dict) else {}
        for pid in pids:
            for x in pv.get(pid) or []:
                m = _FILE_URL.fullmatch(str((x or {}).get("url") or "")) if isinstance(x, dict) else None
                if m:
                    want.add(m.group(1))
    meta = _file_meta(c, owner, want)
    for r in rows:
        pv = r.get("props") if isinstance(r.get("props"), dict) else None
        if not pv:
            continue
        for pid in pids:
            v = pv.get(pid)
            if not isinstance(v, list):
                continue
            out = []
            for x in v:
                fid = str((x or {}).get("url") or "")[7:] if isinstance(x, dict) else ""
                m = meta.get(fid)
                if m:
                    out.append({"url": f"/files/{fid}", "name": (x.get("name") or m["name"]), "size": m["size"], "mime": m["mime"],
                                "image": m["image"]})
            if out:
                pv[pid] = out
            else:
                pv.pop(pid, None)


def out_rows(c, owner, schema, rows, viewer):
    """Personen und Dateien fuer die Antwort aufbereiten (im Speicher) -> {"people": {...}} oder {}."""
    if not has_any(schema) or not rows:
        return {}
    files_out(c, owner, schema, rows)
    pm = people.out_values(c, schema, rows, viewer)
    return {"people": pm} if pm else {}


def text_rows(c, schema, rows):
    """Personen -> Anzeigenamen, Dateien -> Dateinamen (Listen von Texten) - fuer Formeln, Widgets, KI-Kontext."""
    if not has_any(schema) or not rows:
        return
    people.text_values(c, schema, rows)
    pids = file_pids(schema)
    for r in rows:
        pv = r.get("props") if isinstance(r.get("props"), dict) else None
        if not pv:
            continue
        for pid in pids:
            v = pv.get(pid)
            if isinstance(v, list):
                pv[pid] = [str(x.get("name") or "") for x in v if isinstance(x, dict) and x.get("name")]


# --- Text (CSV/Markdown) ---------------------------------------------------------------------------------------------
def _names_text(p, v, ctx):
    """Personen (Anzeigenamen, siehe text_rows) bzw. Dateien (Namen) - Liste von Texten oder Objekten."""
    if not isinstance(v, list):
        return ""
    out = []
    for x in v:
        if isinstance(x, dict):
            x = x.get("name") or ""
        if isinstance(x, str) and x:
            out.append(x)
    return ", ".join(out)


proptext.register("person", _names_text)
proptext.register("files", _names_text)


# --- Mitteilung bei Zuweisung ----------------------------------------------------------------------------------------
def assigned(c, owner, schema, row_id, old, new, actor):
    """Neue Personen in einer Zeile (alt -> neu, kanonisch) melden: bus "row.assigned" (saga.notify legt db.assigned an,
    gebuendelt je Zeile; fremde Personen ueber fed.notify; spaeter Webhooks)."""
    for p in (schema or {}).get("props") or []:
        if not isinstance(p, dict) or p.get("type") != "person":
            continue
        before = set(x for x in ((old or {}).get(p["id"]) or []) if isinstance(x, str))
        added = [x for x in ((new or {}).get(p["id"]) or []) if isinstance(x, str) and x not in before and x != actor]
        if added:
            bus.fire("row.assigned", c, owner=owner, note_id=row_id, pid=p["id"], people=added, actor=actor,
                     notify=p.get("notify") is not False)


# --- Kopieren --------------------------------------------------------------------------------------------------------
def copy_files(c, src_owner, dst_owner, v, copy_fn):
    """Datei-Werte beim Kopieren in eine Datenbank eines ANDEREN Eigentuemers: copy_fn(fid) -> neue ID (oder None)."""
    if src_owner == dst_owner or not isinstance(v, list):
        return v
    out = []
    for x in v:
        m = _FILE_URL.fullmatch(str((x or {}).get("url") or "")) if isinstance(x, dict) else None
        nf = copy_fn(m.group(1)) if m else None
        if nf:
            out.append({"url": f"/files/{nf}", "name": x.get("name") or ""})
    return out


def person_names_json(c, values):
    """Kanonische Kennungen -> Anzeigenamen (JSON-taugliche Liste) - z. B. fuer Webhooks/MCP spaeter."""
    nm = names(c, values)
    return json.loads(json.dumps([nm.get(x) or t("Gelöschte Person") for x in values]))
