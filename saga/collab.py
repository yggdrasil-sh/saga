"""Gleichzeitig bearbeiten, Stufe 2 (2.5, P3): CRDT-Hub und WebSocket /api/collab (Admin-Schalter notes.live).

Protokoll fuer Clients: docs/APP-API-COLLAB.md. Dokumentmodell (Block-JSON <-> Y.Doc): saga/collab_model.py - dieses
Modul kennt davon nur die Schnittstelle (MODEL, SCHEMA_VER, SCHEMA_HASH, build, read, write, roots_ok, new_doc).

Aufbau (ein Prozess, ein Worker - Zustand im Speicher, wie saga.live):
- Je offener Seite ein Raum (_Room): pycrdt-Doc mit dem massgeblichen Stand, verbundene Abos, Sperre fuer Schreiber.
- Ein Update wird zuerst auf eine KOPIE angewendet, das Ergebnis in Block-JSON zurueckgefuehrt und genau wie beim
  Speichern geprueft (pages.clean_blocks, pages.body_checks: Erwaehnungen, synchronisierte Bloecke, Dateien,
  Schaltflaechen-Signaturen, Nextcloud; Groesse NOTE_BLOCKS_MAX; Zustand hoechstens STATE_MAX). Aendert die Pruefung
  etwas (verbotenes Tag, fremde Signatur, unbekannter Knoten ...), wird das Update ABGELEHNT (reject + Vollzustand).
- Angenommene Updates landen zuerst in note_crdt_log (dauerhaft), dann im Raum, dann bei den anderen Abos.
- Verdichtung: nach COMPACT_N Updates oder COMPACT_IDLE s Ruhe -> note_crdt.state, Log gekuerzt.
- Materialisierung: note.body hoechstens MAT_DELAY s nach der letzten Aenderung (und sofort beim letzten Abmelden)
  ueber den normalen Speicherpfad (pages._note_update: rev+1, note.saved, Versionen, note_stale, note_base), damit alle
  Leser (Suche, Veroeffentlichen, Widgets, Export, Foederation v1, Apps) unveraendert bleiben.
- JSON-Schreiber waehrend einer Live-Sitzung (PATCH alter Clients/Apps, Version wiederherstellen, KI, Galerie,
  Datenbanken, blocks/move, synced-Durchschreiben, Nextcloud): alle schreiben ueber note.body; ingest() ist die EINE
  Stelle, die einen neuen body in den Raum uebernimmt (Unterschied als Y-Operationen, an alle verteilt). Aufgerufen ueber
  das Bus-Ereignis note.saved (nach dem Commit) - kein Schreibpfad kommt daran vorbei.
- Rechte: beim sub (Rolle, Sperre), bei Freigabe-Aenderungen, Gruppenwechseln, Sperren und alle CHECK_EVERY s erneut;
  ohne Zugriff -> bye. Rolle view/comment und gesperrte Seiten: nur lesen.
- Titel-Leck: Verweise auf Seiten und Erwaehnungen stehen ohne Text im Zustand; die Materialisierung setzt die Titel
  aus Sicht des Eigentuemers ein (wie restore_links), angezeigt wird beim Client aus der Seitenliste/people."""
import asyncio
import hashlib
import json
import secrets
import struct
import threading
import time

from pycrdt import Doc, get_update

import auth
from db import connect as _db
from saga import bus, collab_model as model
from saga.core import E, _session_user, _tok_hash, device_alive, origin_of, t, user_info
from saga.notes import NOTE_TITLE_MAX
from saga.sharing import RANK, Access, names

PATH = "/api/collab"
PROTO = 1
STATE_MAX = 1 << 20                     # Zustand je Seite hoechstens 1 MB (hart)
MSG_MAX = 256 * 1024                    # eine Nachricht hoechstens 256 KB
MAX_PER_USER = max(1, int(E.get("LIVE_MAX_PER_USER", "5")))      # wie der Live-Kanal (SSE)
MAX_TOTAL = max(10, int(E.get("COLLAB_MAX_TOTAL", "2000")))
SUBS_MAX = 20                           # Seiten je Verbindung
COMPACT_N = 200
COMPACT_IDLE = float(E.get("COLLAB_COMPACT_IDLE", "30"))
MAT_DELAY = float(E.get("COLLAB_MAT_DELAY", "2"))
CHECK_EVERY = max(1.0, float(E.get("COLLAB_CHECK", "25")))     # Sitzung/Rechte erneut pruefen (s)
AW_MAX = 2048                           # Awareness je Abo
AW_EVERY = 0.05                         # hoechstens 20 Awareness-Meldungen je s und Abo
UPD_RATE = 60                           # Updates je s und Abo (danach kurz gebremst)
DROP_DAYS = 30                          # note_crdt ohne Live-Sitzung so lange behalten

# Nachrichtenarten (erstes Byte); Aufbau siehe docs/APP-API-COLLAB.md
T_HELLO, T_SUB, T_UNSUB, T_SYNC1, T_SYNC2, T_UPDATE, T_AW, T_REJECT, T_BYE, T_ERROR, T_PING, T_PONG, T_ACK = range(1, 14)
# Schliesscodes der Verbindung
C_OFF, C_AUTH, C_ORIGIN, C_LIMIT, C_PROTO, C_RESTART = 4403, 4401, 4400, 4429, 4426, 1012

_rooms = {}                             # note_id -> _Room
_conns = {}                             # uid -> [_Conn]
_S = {"loop": None, "tasks": [], "stats": {"rejected": 0, "accepted": 0, "materialized": 0, "ingested": 0}}


def enabled():
    """Admin-Schalter notes.live (Standard aus). Aus: kein WebSocket (Ablehnung), Stufe 1 wie bisher, features ohne live1."""
    return bool((auth.settings().get("notes") or {}).get("live"))


def stats():
    return dict(_S["stats"], rooms=len(_rooms), conns=sum(len(v) for v in _conns.values()))


# =====================================================================================================================
# Kodierung: [art:u8] [note_id:i64 big-endian] [nutzlast] - Nutzlast je Art siehe Doku
# =====================================================================================================================
def _vu(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _rvu(b, i):
    n = s = 0
    while True:
        if i >= len(b) or s > 63:
            raise ValueError("varuint")
        x = b[i]
        i += 1
        n |= (x & 0x7F) << s
        s += 7
        if not x & 0x80:
            return n, i


def _vb(data):
    return _vu(len(data)) + data


def _rvb(b, i):
    n, i = _rvu(b, i)
    if i + n > len(b):
        raise ValueError("laenge")
    return b[i:i + n], i + n


def frame(kind, nid, *parts):
    return bytes([kind]) + struct.pack(">q", nid) + b"".join(_vb(p if isinstance(p, bytes) else json.dumps(p, separators=(",", ":"),
                                                                                                    ensure_ascii=False).encode())
                                                             for p in parts)


def parse(b):
    """-> (art, note_id, [teile als bytes])"""
    if len(b) < 9:
        raise ValueError("kurz")
    kind = b[0]
    nid = struct.unpack(">q", b[1:9])[0]
    parts, i = [], 9
    while i < len(b):
        p, i = _rvb(b, i)
        parts.append(p)
    return kind, nid, parts


def _js(p):
    v = json.loads(p.decode("utf-8")) if p else {}
    return v if isinstance(v, dict) else {}


# =====================================================================================================================
# Raeume
# =====================================================================================================================
class Rejected(Exception):
    def __init__(self, code, msg=""):
        super().__init__(code)
        self.code, self.msg = code, msg


class _Resolver:
    """Texte fuer Verweise/Erwaehnungen beim Materialisieren (Sicht des Eigentuemers, wie restore_links/title_of):
    Verweis = aktueller Titel der Seite des Eigentuemers; fremde/fehlende Seite: bisheriger Text an dieser Stelle (k-ter
    Verweis auf dieselbe ID im Block), sonst der Platzhalter. Erwaehnung: bisheriger Text, sonst aktueller Name."""

    def __init__(self, c, owner, base_doc):
        self.c, self.owner = c, owner
        self.texts = {}
        import re
        rx_a = re.compile(r'<a href="/notes#([1-9][0-9]{0,17})">(.*?)</a>', re.S)
        rx_m = re.compile(r'<span class="hk-mention" data-u="([^"]{1,300})">(.*?)</span>', re.S)
        from saga.notes import _iter_blocks
        for b in _iter_blocks((base_doc or {}).get("blocks") or []):
            s = json.dumps(b.get("data"), ensure_ascii=False)
            s = json.loads(s)
            for txt in _strings(s):
                for m in rx_a.finditer(txt):
                    self.texts.setdefault(("p", b.get("id"), int(m.group(1))), []).append(m.group(2))
                    self.texts.setdefault(("p", None, int(m.group(1))), []).append(m.group(2))
                for m in rx_m.finditer(txt):
                    self.texts.setdefault(("m", b.get("id"), m.group(1)), []).append(m.group(2))
                    self.texts.setdefault(("m", None, m.group(1)), []).append(m.group(2))
        self._titles, self._names = {}, {}

    def page(self, bid, pid, i):
        from html import escape
        lst = self.texts.get(("p", bid, pid)) or self.texts.get(("p", None, pid))
        if lst:                             # bisheriger Text an dieser Stelle (eigener Linktext bleibt, wie restore_links)
            return lst[min(i, len(lst) - 1)]
        if pid not in self._titles:
            r = self.c.execute("SELECT title FROM note WHERE id=? AND uid=?", (pid, self.owner)).fetchone() \
                if isinstance(pid, int) and 0 < pid < 2 ** 63 else None
            self._titles[pid] = escape(r[0] or t("Unbenannt"), quote=False) if r else None
        return self._titles[pid] if self._titles[pid] is not None else escape(t("Seite ohne Zugriff"), quote=False)

    def mention(self, bid, u, i):
        from html import escape
        lst = self.texts.get(("m", bid, u)) or self.texts.get(("m", None, u))
        if lst:
            return lst[min(i, len(lst) - 1)]
        if u not in self._names:
            from saga import mentions
            cu = mentions.canon_of(self.c, u)
            self._names[u] = escape(names(self.c, [cu]).get(cu) or "", quote=False) if cu else ""
        return self._names[u]


def _mask(body):
    """Inhalt (JSON) -> Form im CRDT-Zustand: Autoren von Nextcloud-Bloecken als Handle (nie der Anmeldename - jede Person
    mit Lesezugriff bekommt den ganzen Zustand). Verweis-/Erwaehnungstexte entfernt das Dokumentmodell selbst."""
    from saga import nextcloud
    return _mask_links(nextcloud.mask_local(body, "blocks", ""))


def _mask_links(body):
    """Texte von Seitenverweisen in Feldern, die das Modell als JSON-Wert fuehrt (undurchsichtige/unbekannte Bloecke,
    extra-Felder), leeren - sonst stuende der Titel fremder Seiten im Zustand (Rich-Text entfernt sie das Modell selbst).
    Beim Zurueckfuehren setzt _restore_links die bisherigen Texte wieder ein."""
    if "/notes#" not in (body or ""):
        return body
    from saga.sharing import _NOTE_LINK, _walk_strings
    d = json.loads(body)

    def fn(s, _blk=None):
        return _NOTE_LINK.sub(lambda m: f'<a href="/notes#{m.group(1)}"></a>', s) if "/notes#" in s else s
    return json.dumps(_walk_strings(d, fn), ensure_ascii=False)


def _restore_links(c, room, body):
    """Gegenstueck zu _mask_links: Verweistexte aus dem zuletzt abgeglichenen Stand (Eigentuemer-Sicht), neue Verweise
    wie beim Speichern durch Nicht-Eigentuemer (sharing.restore_links)."""
    if "/notes#" not in json.dumps(body, ensure_ascii=False):
        return body
    from saga.sharing import _NOTE_LINK, _walk_strings
    res = _Resolver(c, room.owner, room.base)
    seen = {}

    def fn(s, blk=None):
        def rep(m):
            if m.group(2):
                return m.group(0)
            pid = int(m.group(1))
            k = seen.get((blk, pid), 0)
            seen[(blk, pid)] = k + 1
            return f'<a href="/notes#{pid}">{res.page(blk, pid, k)}</a>'
        return _NOTE_LINK.sub(rep, s) if "/notes#" in s else s
    out = _walk_strings(body, fn)
    body.clear()
    body.update(out)
    return body


def _unmask(c, doc):
    """Gegenstueck zu _mask (in place): Handle -> Anmeldename, damit Signaturpruefung und Speichern wie bisher laufen.
    Ein Handle ohne gueltige Signatur fuer das Konto faellt bei pages.body_checks (nextcloud.check_body) durch."""
    from saga.core import HANDLE_RE, resolve_ref
    from saga.notes import _iter_blocks
    for b in _iter_blocks(doc.get("blocks") or []):
        if b.get("type") == "nextcloud" and isinstance(b.get("data"), dict):
            a = b["data"].get("author")
            if isinstance(a, str) and HANDLE_RE.fullmatch(a):
                real = resolve_ref(c, a)
                if real:
                    b["data"]["author"] = real
    return doc


def _strings(v):
    if isinstance(v, str):
        yield v
    elif isinstance(v, list):
        for x in v:
            yield from _strings(x)
    elif isinstance(v, dict):
        for x in v.values():
            yield from _strings(x)


class _Room:
    def __init__(self, nid, owner):
        self.nid, self.owner = nid, owner
        self.doc = None
        self.gen = ""
        self.subs = {}                    # _Sub -> True
        self.lock = threading.Lock()      # Schreiber (Updates, ingest, Materialisierung) nacheinander
        self.rev = 0                      # note.rev des Stands, mit dem der Raum zuletzt abgeglichen ist
        self.base = None                  # Block-Dokument (dict) dieses Stands (Durchreichung, Resolver)
        self.cur = None                   # zuletzt gepruefter Stand des Raums (JSON) - Grundlage der naechsten Pruefung
        self.base_title = ""
        self.dirty_at = 0.0               # letzte Aenderung, die noch nicht in note.body steht
        self.editor = None                # wer zuletzt geschrieben hat (updated_by beim Materialisieren)
        self.n_log, self.last = 0, time.time()
        self.seq = 0
        self.owners = {}                  # client_id -> uid
        self.mat_task = None
        self.closed = False


def _load_room(nid):
    """Im Thread: Raum aus note_crdt laden bzw. aus note.body neu aufbauen. -> _Room oder None (keine Block-Seite)."""
    with _db() as c, c:
        r = c.execute("SELECT uid, body, format, rev, title FROM note WHERE id=? AND deleted_at IS NULL", (nid,)).fetchone()
        if not r or r[2] != "blocks":
            return None
        owner, body, _, rev, title = r
        room = _Room(nid, owner)
        from saga.notes import ensure_ids
        body = ensure_ids(body, "blocks", nid) or '{"blocks": []}'
        st = c.execute("SELECT model, schema_ver, gen, state, rev, base, base_title, updated, mat, owners FROM note_crdt WHERE note_id=?",
                       (nid,)).fetchone()
        logs = []
        if st and st[0] == model.MODEL and st[1] == model.SCHEMA_VER:
            logs = [bytes(x[0]) for x in c.execute("SELECT upd FROM note_crdt_log WHERE note_id=? ORDER BY seq", (nid,)).fetchall()]
            d = model.new_doc()
            d.apply_update(bytes(st[3]))
            for u in logs:
                d.apply_update(u)
            room.doc, room.gen = d, st[2]
            room.rev, room.base_title = st[4], st[6] or ""
            room.base = _jl(st[5])
            room.dirty_at = st[7] if st[7] > st[8] else 0.0
            try:
                room.owners = {int(k): v for k, v in json.loads(st[9] or "{}").items()}
            except (ValueError, TypeError):
                room.owners = {}
            room.seq = c.execute("SELECT COALESCE(MAX(seq), 0) FROM note_crdt_log WHERE note_id=?", (nid,)).fetchone()[0]
            room.n_log = len(logs)
            room.owners[room.doc.client_id] = ""          # Kennung des Servers in diesem Prozess
        else:
            # erstes Live-Oeffnen bzw. anderes Modell: deterministisch aus body (gleicher Inhalt = gleicher Anfangszustand)
            if st:
                c.execute("DELETE FROM note_crdt_log WHERE note_id=?", (nid,))
            upd = model.build(_mask(body), title or "")
            d = model.new_doc()
            d.apply_update(upd)
            room.doc = d
            room.gen = secrets.token_hex(8)
            room.rev, room.base, room.base_title = rev, json.loads(body), title or ""
            room.owners = {room.doc.client_id: ""}
            _save_state(c, room)
            return room
        return room                       # note.body neuer (geschrieben, waehrend der Raum zu war): _get_room gleicht ab


def _jl(s):
    try:
        v = json.loads(s) if s else None
        return v if isinstance(v, dict) else None
    except ValueError:
        return None


def _save_state(c, room, mat=None):
    now = time.time()
    upd = room.doc.get_update()
    c.execute("INSERT INTO note_crdt (note_id, model, schema_ver, gen, state, sv, rev, base, base_title, updated, mat, used, owners) "
              "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (note_id) DO UPDATE SET model=excluded.model, schema_ver=excluded.schema_ver, "
              "gen=excluded.gen, state=excluded.state, sv=excluded.sv, rev=excluded.rev, base=excluded.base, base_title=excluded.base_title, "
              "updated=excluded.updated, mat=excluded.mat, used=excluded.used, owners=excluded.owners",
              (room.nid, model.MODEL, model.SCHEMA_VER, room.gen, upd, room.doc.get_state(), room.rev,
               json.dumps(room.base, ensure_ascii=False) if room.base is not None else None, room.base_title,
               room.dirty_at or now, (now if not room.dirty_at else 0) if mat is None else mat, now,
               json.dumps({str(k): v for k, v in room.owners.items()})))
    c.execute("DELETE FROM note_crdt_log WHERE note_id=? AND seq<=?", (room.nid, room.seq))
    room.n_log = 0


# =====================================================================================================================
# Pruefen und Annehmen
# =====================================================================================================================
def _role_now(c, uid, nid):
    acc = Access(c, uid)
    return acc.role(nid), acc


def _sv(b):
    """Zustandsvektor (Bytes) -> {client_id: clock}"""
    n, i = _rvu(b, 0)
    out = {}
    for _ in range(n):
        cid, i = _rvu(b, i)
        clk, i = _rvu(b, i)
        out[cid] = clk
    return out


def _read_body(c, room, doc):
    """Doc -> (Block-Dokument wie gespeichert als JSON, Titel) - nur Schema und clean_blocks, ohne Rechte.
    Rejected, wenn der Zustand nicht zum Schema passt oder clean_blocks etwas aendern muesste."""
    from saga import pages
    if not model.roots_ok(doc):
        raise Rejected("type", "Wurzel")
    try:
        blocks_doc, title = model.read(doc, _Resolver(c, room.owner, room.base), _mask_doc(room.base))
        _unmask(c, blocks_doc)
        _restore_links(c, room, blocks_doc)
    except model.ModelError as ex:
        raise Rejected("type", str(ex)) from None
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as ex:
        raise Rejected("type", type(ex).__name__) from None
    raw = json.dumps(blocks_doc, ensure_ascii=False)
    try:
        if len(title) > NOTE_TITLE_MAX or title != pages._title_in(title):
            raise Rejected("title")
    except ValueError:
        raise Rejected("title") from None
    try:
        body, _ = pages.clean_blocks(raw)
    except pages.TooLarge:
        raise Rejected("size") from None
    except ValueError as ex:
        raise Rejected("invalid", str(ex)) from None
    if json.loads(body)["blocks"] != json.loads(raw)["blocks"]:
        raise Rejected("sanitize")
    return body, title


def _server_fields(doc):
    """Felder, die nur der Server setzt (Signatur von Vorlagen-Schaltflaechen, Nextcloud-Autor/-Signatur), aus doc
    herausnehmen -> [(block_id, art, wert)]"""
    from saga.notes import _iter_blocks
    out = []
    for b in _iter_blocks(doc.get("blocks") or []):
        d = b.get("data") if isinstance(b.get("data"), dict) else None
        if d is None:
            continue
        if b.get("type") == "buttons":
            for i, bt in enumerate(d.get("buttons") or []):
                if isinstance(bt, dict) and "sig" in bt:
                    out.append((b.get("id"), f"sig{i}", bt.pop("sig")))
        elif b.get("type") == "nextcloud":
            for k in ("sig", "author"):
                if k in d:
                    out.append((b.get("id"), k, d.pop(k)))
    return out


def _mask_doc(d):
    if d is None or ('"nextcloud"' not in json.dumps(d) and "/notes#" not in json.dumps(d)):
        return d
    return json.loads(_mask(json.dumps(d, ensure_ascii=False)))


def _cur_body(c, room):
    if room.cur is None:
        room.cur, _ = _read_body(c, room, room.doc)
    return room.cur


def _check(c, room, doc, editor, acc, caps):
    """Doc (nach einem Update) wie beim Speichern pruefen -> (body, titel, korrektur|None). korrektur = Block-Dokument,
    wenn der Server nur Felder ergaenzen muss, die allein er setzt (Signatur einer eigenen neuen Vorlagen-Schaltflaeche,
    Autor/Signatur eines eigenen Nextcloud-Blocks): er schreibt sie dann selbst als Update. Alles andere -> Rejected."""
    from saga import nextcloud, pages
    body, title = _read_body(c, room, doc)
    old = _cur_body(c, room)
    try:
        checked, _plan = pages.body_checks(c, room.owner, room.nid, editor, old, "blocks", body, caps, acc)
    except nextcloud.Rejected as ex:
        raise Rejected("signature", str(ex)) from None
    except ValueError as ex:
        raise Rejected("forbidden", str(ex)) from None
    a, b = json.loads(body), json.loads(checked)
    if a["blocks"] == b["blocks"]:
        return body, title, None
    sent, want = _server_fields(a), _server_fields(b)
    if a["blocks"] == b["blocks"]:          # Unterschied nur in Server-Feldern
        have = {(x[0], x[1]): x[2] for x in sent}
        # vom Client gesendete Werte muessen die des Servers sein (bzw. fehlen) - sonst gefaelscht
        if all(have.get((x[0], x[1]), x[2]) == x[2] for x in want) and {k for k in have} <= {(x[0], x[1]) for x in want}:
            return checked, title, json.loads(checked)
        raise Rejected("signature")
    if any(t_ in body for t_ in ('"buttons"', '"nextcloud"')) and _only_special(json.loads(body), json.loads(checked)):
        raise Rejected("signature")
    raise Rejected("forbidden")


def _only_special(a, b):
    """Unterscheiden sich a und b nur in Schaltflaechen- bzw. Nextcloud-Bloecken?"""
    from saga.notes import _iter_blocks

    def strip(doc):
        return [x.get("type") if x.get("type") in ("buttons", "nextcloud") else json.dumps(x, sort_keys=True)
                for x in _iter_blocks(doc.get("blocks") or [])]
    return strip(a) == strip(b)


def _apply_update(room, sub, update):
    """Im Thread, unter room.lock: Update pruefen, dauerhaft ablegen, im Raum anwenden.
    -> (geaendert?, update fuer die anderen, korrektur des Servers fuer alle | None)"""
    if room.closed:
        raise Rejected("gone")
    if len(update) > MSG_MAX:
        raise Rejected("size")
    full0 = room.doc.get_update()
    sv0 = _sv(room.doc.get_state())
    tmp = Doc()
    tmp.apply_update(full0)
    try:
        tmp.apply_update(update)
        rest = get_update(update, tmp.get_state())
    except Exception:  # noqa: BLE001 - kaputtes Update
        raise Rejected("invalid") from None
    if rest and _rvu(rest, 0)[0] > 0:
        raise Rejected("pending")           # Eintraege, die an Unbekanntem haengen (Luecke): Client gleicht neu ab (sync1)
    sv1 = _sv(tmp.get_state())
    full = tmp.get_update()
    if full == full0:
        return False, None, None            # schon bekannt (Duplikat)
    if len(full) > STATE_MAX:
        raise Rejected("size")
    new_ids = [cid for cid, clk in sv1.items() if clk > sv0.get(cid, 0)]
    for cid in new_ids:                    # niemand schreibt unter der Kennung eines anderen (oder des Servers)
        who = room.owners.get(cid)
        if cid == model.BASE_CLIENT or (who is not None and who != sub.uid):
            raise Rejected("client")
    with _db() as c, c:
        role, acc = _role_now(c, sub.uid, room.nid)
        lk = c.execute("SELECT locked FROM note WHERE id=? AND deleted_at IS NULL", (room.nid,)).fetchone()
        if not role or not lk:
            raise Rejected("gone")
        if RANK.get(role, 0) < RANK["edit"] or lk[0]:
            raise Rejected("readonly")
        body, _title, fix = _check(c, room, tmp, sub.uid, acc, sub.caps)
        corr = None
        if fix is not None:                 # Server-Felder selbst schreiben (eigenes Update, an alle)
            sv_t = tmp.get_state()
            srv = Doc(client_id=room.doc.client_id)
            srv.apply_update(tmp.get_update())
            model.write(srv, json.loads(_mask(json.dumps(fix, ensure_ascii=False))))
            corr = srv.get_update(sv_t)
            tmp.apply_update(corr)
            body, _title = _read_body(c, room, tmp)
        now = time.time()
        room.seq += 1
        c.execute("INSERT INTO note_crdt_log (note_id, seq, upd, uid, at) VALUES (?,?,?,?,?)", (room.nid, room.seq, update, sub.uid, now))
        if corr is not None:
            room.seq += 1
            c.execute("INSERT INTO note_crdt_log (note_id, seq, upd, uid, at) VALUES (?,?,?,?,?)", (room.nid, room.seq, corr, "", now))
        for cid in new_ids:
            room.owners.setdefault(cid, sub.uid)
        c.execute("UPDATE note_crdt SET updated=?, used=?, owners=? WHERE note_id=?",
                  (now, now, json.dumps({str(k): v for k, v in room.owners.items()}), room.nid))
    room.doc.apply_update(update)
    if corr is not None:
        room.doc.apply_update(corr)
    room.dirty_at = now
    room.cur = body
    room.editor = sub.uid
    room.n_log += 2 if corr is not None else 1
    room.last = time.time()
    _S["stats"]["accepted"] += 1
    return True, update, corr


# =====================================================================================================================
# Materialisieren und Uebernehmen (JSON-Schreiber)
# =====================================================================================================================
def _materialize(room, force=False):
    """Im Thread: note.body = Stand des Raums (ueber pages._note_update, als Eigentuemer, trusted). -> True, wenn geschrieben."""
    from saga import pages
    with room.lock:
        if not room.dirty_at or room.closed:
            return False
        if not force and time.time() - room.dirty_at < MAT_DELAY:
            return False
        try:
            with _db() as c:
                body, title = _read_body(c, room, room.doc)
                cur = c.execute("SELECT rev, body, title FROM note WHERE id=? AND deleted_at IS NULL", (room.nid,)).fetchone()
            if not cur:
                return False
            b = {"body": body, "format": "blocks", "rev": cur[0]}
            if title != (cur[2] or ""):
                b["title"] = title
            same_body = json.loads(cur[1] or "{}").get("blocks") == json.loads(body)["blocks"] if cur[1] else False
            if same_body and "title" not in b:
                room.dirty_at = 0
                room.rev, room.base, room.base_title = cur[0], json.loads(body), title
                with _db() as c, c:
                    _save_state(c, room)
                return False
            try:
                r = pages._note_update(room.owner, room.nid, b, origin="collab", editor=room.editor or room.owner, trusted=True)
            except pages._Reply as rp:
                r = rp.resp
            if getattr(r, "status_code", 500) != 200:
                print(f"collab: Seite {room.nid}: Materialisieren {getattr(r, 'status_code', '?')} {getattr(r, 'body', b'')[:200]!r}", flush=True)
                return False
            new_rev = json.loads(r.body).get("rev")
            with _db() as c, c:
                nb = c.execute("SELECT body, title FROM note WHERE id=?", (room.nid,)).fetchone()
                room.rev, room.base, room.base_title = new_rev, json.loads(nb[0]), nb[1] or ""
                room.dirty_at = 0
                _save_state(c, room)
            _S["stats"]["materialized"] += 1
            return True
        except Rejected as ex:
            print(f"collab: Seite {room.nid}: Stand nicht materialisierbar ({ex.code} {ex.msg})", flush=True)
            return False


def ingest(nid, rev=None):
    """Die EINE Stelle fuer JSON-Schreiber waehrend einer Live-Sitzung: note.body hat sich ausserhalb des CRDT geaendert
    (PATCH alter Clients/Apps, Version wiederherstellen, KI, Galerie, Datenbanken, blocks/move, synced, Nextcloud,
    Foederation v1). Der neue Stand wird als Differenz gegen den letzten abgeglichenen Stand ins Doc geschrieben:
    gleichzeitige Live-Aenderungen, die noch nicht materialisiert sind, werden dabei per Stufe-1-Merge (saga.merge)
    erhalten. -> Update fuer die Abos oder None. Im Thread."""
    room = _rooms.get(nid)
    if room is None or room.doc is None:
        return None
    with room.lock:
        if room.closed:
            return None
        with _db() as c:
            r = c.execute("SELECT body, format, rev, title, uid, deleted_at FROM note WHERE id=?", (nid,)).fetchone()
        if not r or r[5] is not None or r[1] != "blocks":
            room.closed = True
            return "gone"
        from saga.notes import ensure_ids
        theirs = json.loads(ensure_ids(r[0], "blocks", nid) or '{"blocks": []}')
        if r[2] == room.rev and (theirs == room.base or room.base is None) and (r[3] or "") == room.base_title:
            return None
        from saga import merge
        title = r[3] or ""
        sv0 = room.doc.get_state()
        if room.dirty_at and room.base is not None:
            # Live-Stand noch nicht in body: drei Wege (Basis = letzter abgeglichener Stand, meins = live, deren = body)
            try:
                with _db() as c:
                    live, live_title = model.read(room.doc, _Resolver(c, room.owner, room.base), _mask_doc(room.base))
                    _unmask(c, live)
                    _restore_links(c, room, live)
                target, _ = merge.merge_doc(room.base, live, theirs, None, {"by": "", "name": ""}, time.time())
                title = merge.merge_title(room.base_title, live_title, title)[0]
            except Exception as ex:  # noqa: BLE001
                print(f"collab: Seite {nid}: Zusammenfuehren beim Uebernehmen: {type(ex).__name__}: {ex}", flush=True)
                target = theirs
        else:
            target = theirs
        model.write(room.doc, _mask_doc(target), title)
        upd = room.doc.get_update(sv0)
        room.rev, room.base, room.base_title = r[2], theirs, r[3] or ""
        if target is not theirs and json.dumps(target, sort_keys=True) != json.dumps(theirs, sort_keys=True):
            room.dirty_at = time.time()          # zusammengefuehrt: Ergebnis spaeter materialisieren
        else:
            room.dirty_at = 0
        room.cur = None
        with _db() as c, c:
            _save_state(c, room)
        _S["stats"]["ingested"] += 1
        return upd if len(upd) > 2 else None


# =====================================================================================================================
# Verbindungen
# =====================================================================================================================
class _Sub:
    __slots__ = ("conn", "nid", "uid", "role", "ro", "aw", "aw_at", "caps", "name", "color", "n", "n_at")

    def __init__(self, conn, nid, role, ro, caps):
        self.conn, self.nid, self.uid, self.role, self.ro = conn, nid, conn.uid, role, ro
        self.aw, self.aw_at, self.caps = None, 0.0, caps
        self.name, self.color = "", ""
        self.n, self.n_at = 0, 0.0


class _Conn:
    def __init__(self, ws, uid, kind, check, origin):
        self.ws, self.uid, self.kind, self.check, self.origin = ws, uid, kind, check, origin
        self.q = asyncio.Queue(512)
        self.subs = {}                    # nid -> _Sub
        self.closed = False
        self.code = 1000

    def send(self, data):
        if self.closed:
            return
        try:
            self.q.put_nowait(data)
        except asyncio.QueueFull:
            self.kill(C_LIMIT)

    def kill(self, code):
        if not self.closed:
            self.closed, self.code = True, code
            try:
                self.q.put_nowait(None)
            except asyncio.QueueFull:
                pass


def _who(scope):
    """(uid, art, pruefung, herkunft) aus Sitzung bzw. Geraete-/App-Schluessel (wie /api/events) oder None."""
    hdr = ""
    for k, v in scope.get("headers") or ():
        if k == b"authorization":
            hdr = v.decode("latin-1")
    if hdr.startswith("Bearer hkd_") and len(hdr) <= 130:
        h = _tok_hash(hdr[7:].strip())

        def dev_ok(want_id=False):
            with _db() as c:
                r = c.execute("SELECT uid, id FROM device_login WHERE hash=?", (h,)).fetchone()
            uid_ = r[0] if r and user_info(r[0]) is not None else None
            return (uid_, r[1] if uid_ else None) if want_id else uid_
        uid, did = dev_ok(True)
        return (uid, "app", lambda: dev_ok() == uid, f"d{did}") if uid else None

    class _R:                             # Sitzung aus dem scope (SessionMiddleware hat sie gelesen)
        session = scope.get("session") or {}
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers") or ()}
        query_params = {}
    req = _R()
    uid = _session_user(req)
    if not uid:
        return None
    pwc = float(req.session.get("pwc") or 0)
    did = req.session.get("did")

    def ok():
        info = user_info(uid)
        return info is not None and info["epoch"] <= pwc and (did is None or device_alive(uid, did))
    return uid, "web", ok, origin_of(req, "")


def _origin_ok(scope):
    """Browser: Origin muss die eigene Adresse sein (WebSocket kennt kein CORS; das Cookie geht sonst von jeder Seite mit).
    Apps senden keinen Origin."""
    from saga.core import BASE_URL
    o = next((v.decode("latin-1") for k, v in scope.get("headers") or () if k == b"origin"), None)
    if o is None:
        return True
    return o.rstrip("/").lower() == BASE_URL.lower()


def _sub_check(uid, nid, schema):
    """Im Thread: darf uid die Seite live oeffnen? -> (rolle, nur_lesen, besitzer) oder Fehlercode (str)."""
    with _db() as c:
        acc = Access(c, uid)
        role = acc.role(nid)
        if not role:
            return "notfound"
        r = c.execute("SELECT format, locked, uid FROM note WHERE id=? AND deleted_at IS NULL", (nid,)).fetchone()
        if not r:
            return "notfound"
        if r[0] != "blocks":
            return "format"
    ro = RANK.get(role, 0) < RANK["edit"] or bool(r[1])
    if not (isinstance(schema, dict) and schema.get("model") == model.MODEL and schema.get("ver") == model.SCHEMA_VER
            and schema.get("hash") == model.SCHEMA_HASH):
        ro = "schema"                       # anderes Schema (Risiko R1): nur lesen, Client zeigt "Neu laden"
    return role, ro, r[2]


def _display(uid):
    with _db() as c:
        nm = names(c, [uid])
    from saga.live import pcolor
    return nm.get(uid) or "", pcolor(uid)


async def _get_room(nid):
    room = _rooms.get(nid)
    if room is None:
        room = await asyncio.to_thread(_load_room, nid)
        if room is None:
            return None
        if nid in _rooms:                   # gleichzeitig geladen: der erste gewinnt
            return _rooms[nid]
        _rooms[nid] = room
        if room.dirty_at:                   # nach Neustart: noch nicht materialisierte Aenderungen nachholen
            _schedule_mat(room)
    # body ausserhalb geaendert, waehrend der Raum zu war bzw. seitdem: uebernehmen
    upd = await asyncio.to_thread(ingest, nid)
    if upd and upd != "gone":
        _fanout(room, frame(T_UPDATE, nid, upd), None)
    return room


async def _snap(room, sv=None, state=False):
    """Zustand des Raums lesen (im Thread unter der Sperre: ein Schreiber koennte gerade daran arbeiten)."""
    def run():
        with room.lock:
            if state:
                return room.doc.get_state()
            try:
                return room.doc.get_update(sv) if sv is not None else room.doc.get_update()
            except ValueError:
                return room.doc.get_update()
    return await asyncio.to_thread(run)


def _fanout(room, data, skip):
    for s in list(room.subs):
        if s is not skip:
            s.conn.send(data)


def _schedule_mat(room):
    if room.mat_task is not None and not room.mat_task.done():
        return

    async def run():
        while room.dirty_at and not room.closed:
            wait = MAT_DELAY - (time.time() - room.dirty_at)
            if wait > 0:
                await asyncio.sleep(wait)
                continue
            try:
                done = await asyncio.to_thread(_materialize, room)
                if not done and room.dirty_at and time.time() - room.dirty_at >= MAT_DELAY:
                    await asyncio.sleep(5)       # abgelehnt (z. B. Datei weg): nicht im 0,2-s-Takt wiederholen
            except Exception as ex:  # noqa: BLE001 - Datenbank kurz weg: spaeter erneut
                print(f"collab: Materialisieren {room.nid}: {type(ex).__name__}: {ex}", flush=True)
                await asyncio.sleep(5)
            await asyncio.sleep(0.2)
    room.mat_task = asyncio.get_running_loop().create_task(run())


async def _close_room_if_idle(room):
    if room.subs or _rooms.get(room.nid) is not room:
        return
    try:
        if room.dirty_at:
            await asyncio.to_thread(_materialize, room, True)
        if room.n_log:
            def comp():
                with room.lock, _db() as c, c:
                    _save_state(c, room, mat=0 if room.dirty_at else None)
            await asyncio.to_thread(comp)
    except Exception as ex:  # noqa: BLE001
        print(f"collab: Schliessen {room.nid}: {type(ex).__name__}: {ex}", flush=True)
    if not room.subs and _rooms.get(room.nid) is room:
        _rooms.pop(room.nid, None)
        if room.mat_task is not None:
            room.mat_task.cancel()


def _aw_clean(raw, sub):
    """Awareness eines Abos: nur {cursor, sel} (relative Positionen, undurchsichtig, klein) - Name/Farbe setzt der Server."""
    try:
        a = json.loads(raw.decode("utf-8")) if raw else None
    except (ValueError, UnicodeDecodeError):
        return None
    if a is None:
        return {}
    if not isinstance(a, dict):
        return None
    out = {}
    for k in ("cursor", "sel", "block", "typing"):
        if k in a:
            out[k] = a[k]
    s = json.dumps(out, separators=(",", ":"))
    return out if len(s) <= AW_MAX else None


def _aw_frame(sub):
    st = None if sub.aw is None else dict(sub.aw, name=sub.name, color=sub.color, me=False)
    return frame(T_AW, sub.nid, {"sid": id(sub) & 0xFFFFFFFF, "state": st})


async def _handle(conn, kind, nid, parts):
    sub = conn.subs.get(nid)
    if kind == T_PING:
        conn.send(frame(T_PONG, nid))
        return
    if kind == T_SUB:
        if sub is not None:
            return
        if len(conn.subs) >= SUBS_MAX:
            conn.send(frame(T_ERROR, nid, {"code": "limit"}))
            return
        if nid <= 0:
            conn.send(frame(T_ERROR, nid, {"code": "federated"}))      # Seiten anderer Server: Stufe 1 (collab1 folgt)
            return
        opts = _js(parts[0]) if parts else {}
        chk = await asyncio.to_thread(_sub_check, conn.uid, nid, opts.get("schema"))
        if isinstance(chk, str):
            conn.send(frame(T_ERROR, nid, {"code": chk}))
            return
        role, ro, _owner = chk
        room = await _get_room(nid)
        if room is None:
            conn.send(frame(T_ERROR, nid, {"code": "format"}))
            return
        caps = frozenset(x for x in str(opts.get("caps") or "").split(",") if x) & frozenset(("inline2", "cols6", "merge1", "sync1"))
        s = _Sub(conn, nid, role, ro, caps or frozenset(("inline2", "cols6", "merge1")))
        s.name, s.color = await asyncio.to_thread(_display, conn.uid)
        conn.subs[nid] = s
        room.subs[s] = True
        info = {"proto": PROTO, "gen": room.gen, "role": role, "ro": bool(ro), "why": ro if isinstance(ro, str) else ("locked" if ro and role in ("owner", "edit") else None),
                "schema": {"model": model.MODEL, "ver": model.SCHEMA_VER, "hash": model.SCHEMA_HASH}, "me": id(s) & 0xFFFFFFFF,
                "name": s.name, "color": s.color}
        conn.send(frame(T_HELLO, nid, info))
        conn.send(frame(T_SYNC1, nid, await _snap(room, state=True)))
        sv = parts[1] if len(parts) > 1 and opts.get("gen") == room.gen else None
        conn.send(frame(T_SYNC2, nid, await _snap(room, sv)))
        for o in list(room.subs):
            if o is not s and o.aw is not None:
                conn.send(_aw_frame(o))
        return
    if sub is None:
        conn.send(frame(T_ERROR, nid, {"code": "nosub"}))
        return
    room = _rooms.get(nid)
    if room is None:
        conn.send(frame(T_BYE, nid, {"reason": "gone"}))
        _drop_sub(sub)
        return
    if kind == T_UNSUB:
        _drop_sub(sub)
        return
    if kind == T_SYNC1:
        conn.send(frame(T_SYNC2, nid, await _snap(room, parts[0] if parts else None)))
        return
    if kind in (T_SYNC2, T_UPDATE):
        upd = parts[0] if parts else b""
        ref = parts[1] if len(parts) > 1 else b""
        if not upd or len(upd) <= 2:
            return
        if sub.ro:
            conn.send(frame(T_REJECT, nid, {"code": "readonly", "ref": ref.decode("ascii", "replace")[:40]}, await _snap(room)))
            return
        now = time.monotonic()
        if now - sub.n_at >= 1:
            sub.n, sub.n_at = 0, now
        sub.n += 1
        if sub.n > UPD_RATE:
            await asyncio.sleep(0.05)
        try:
            def run():
                with room.lock:
                    return _apply_update(room, sub, upd)
            changed, out, corr = await asyncio.to_thread(run)
        except Rejected as ex:
            _S["stats"]["rejected"] += 1
            if ex.code == "gone":
                conn.send(frame(T_BYE, nid, {"reason": "gone"}))
                _drop_sub(sub)
                return
            conn.send(frame(T_REJECT, nid, {"code": ex.code, "ref": ref.decode("ascii", "replace")[:40]}, await _snap(room)))
            if ex.code == "readonly":
                sub.ro = True
            return
        except Exception as ex:  # noqa: BLE001 - Datenbank weg o. ae.: nicht angenommen, Client wiederholt nach Neuaufbau
            print(f"collab: Update {nid}: {type(ex).__name__}: {ex}", flush=True)
            conn.send(frame(T_REJECT, nid, {"code": "retry", "ref": ref.decode("ascii", "replace")[:40]}, await _snap(room)))
            return
        if ref:
            conn.send(frame(T_ACK, nid, ref))
        if changed:
            _fanout(room, frame(T_UPDATE, nid, out), sub)
            if corr is not None:
                _fanout(room, frame(T_UPDATE, nid, corr), None)
            _schedule_mat(room)
        return
    if kind == T_AW:
        now = time.monotonic()
        if now - sub.aw_at < AW_EVERY:
            return
        sub.aw_at = now
        a = _aw_clean(parts[0] if parts else b"", sub)
        if a is None:
            return
        sub.aw = a or None
        _fanout(room, _aw_frame(sub), sub)
        return
    conn.send(frame(T_ERROR, nid, {"code": "kind"}))


def _drop_sub(sub):
    conn = sub.conn
    if conn.subs.get(sub.nid) is sub:
        conn.subs.pop(sub.nid, None)
    room = _rooms.get(sub.nid)
    if room is not None and sub in room.subs:
        room.subs.pop(sub, None)
        if sub.aw is not None:
            sub.aw = None
            _fanout(room, _aw_frame(sub), sub)
        if not room.subs:
            asyncio.get_running_loop().create_task(_close_room_if_idle(room))


def _recheck(uid_subs):
    """Im Thread: [(sub, uid, nid)] -> {sub: (rolle, gesperrt) | None}"""
    out = {}
    with _db() as c:
        accs = {}
        for s, uid, nid in uid_subs:
            acc = accs.setdefault(uid, Access(c, uid))
            role = acc.role(nid)
            r = c.execute("SELECT locked FROM note WHERE id=? AND deleted_at IS NULL", (nid,)).fetchone()
            out[s] = (role, bool(r[0])) if role and r else None
    return out


async def recheck(uids=None, nid=None):
    """Rechte der Abos erneut pruefen (Freigabe geaendert, Gruppen, Sperre, Papierkorb): ohne Zugriff -> bye,
    weniger Rechte bzw. gesperrt -> nur lesen (hello mit ro)."""
    lst = [(s, s.uid, s.nid) for room in list(_rooms.values()) if nid is None or room.nid == nid for s in list(room.subs)
           if uids is None or s.uid in uids]
    if not lst:
        return
    try:
        res = await asyncio.to_thread(_recheck, lst)
    except Exception:  # noqa: BLE001 - Datenbank weg: spaeter erneut
        return
    for s, _, nid_ in lst:
        r = res.get(s)
        if r is None:
            s.conn.send(frame(T_BYE, nid_, {"reason": "access"}))
            _drop_sub(s)
            continue
        role, locked = r
        ro = RANK.get(role, 0) < RANK["edit"] or locked
        if s.ro == "schema":
            continue
        if bool(ro) != bool(s.ro) or role != s.role:
            s.ro, s.role = ro, role
            s.conn.send(frame(T_HELLO, nid_, {"proto": PROTO, "gen": _rooms[nid_].gen if nid_ in _rooms else "", "role": role,
                                              "ro": bool(ro), "why": "locked" if locked else None, "update": True}))


# --- Bus: Ereignisse aus Transaktionen -------------------------------------------------------------------------------
def _in_loop(fn, *a):
    loop = _S["loop"]
    if loop is None:
        return
    try:
        loop.call_soon_threadsafe(lambda: loop.create_task(fn(*a)))
    except RuntimeError:
        pass


async def _ingest_async(nid):
    room = _rooms.get(nid)
    if room is None:
        return
    try:
        upd = await asyncio.to_thread(ingest, nid)
    except Exception as ex:  # noqa: BLE001
        print(f"collab: Uebernehmen {nid}: {type(ex).__name__}: {ex}", flush=True)
        return
    if upd == "gone":
        for s in list(room.subs):
            s.conn.send(frame(T_BYE, nid, {"reason": "gone"}))
            _drop_sub(s)
        _rooms.pop(nid, None)
        return
    if upd:
        _fanout(room, frame(T_UPDATE, nid, upd), None)
    await recheck(nid=nid)                 # Sperre/Papierkorb


def _on_saved(c, note_id, row=None, **_):
    """note.saved (jeder Speicherpfad, nach dem Commit) bzw. note.body (Inhalt ohne neue Revision geaendert): offenen Raum
    abgleichen. Die eigene Materialisierung ist darin ein Leerlauf (gleicher Stand)."""
    if row is not None or note_id not in _rooms:
        return
    after = getattr(c, "after_commit", None)
    if after is not None:
        after(lambda: _in_loop(_ingest_async, note_id))


def _on_share(c, owner=None, note_id=None, **_):
    after = getattr(c, "after_commit", None)
    if after is not None and _rooms:
        after(lambda: _in_loop(recheck))


bus.on("note.saved", _on_saved)
bus.on("note.body", _on_saved)
bus.on("share.set", _on_share)
bus.on("share.removed", _on_share)


def _live_hook(kind, payload):
    """saga.live (Event-Schleife): Gruppen eines Kontos, Konto geloescht, Freigaben -> Abos der Konten neu pruefen."""
    if not _rooms:
        return
    uids = set(payload.get("uids") or ()) | ({payload["uid"]} if payload.get("uid") else set())
    asyncio.get_running_loop().create_task(recheck(uids or None))


def _install():
    from saga import live
    live.COLLAB_HOOK = _live_hook


_install()


# =====================================================================================================================
# WebSocket
# =====================================================================================================================
class Front:
    """ASGI: WebSocket /api/collab innerhalb der Sitzungs-Middleware (Cookie), ohne FastAPI-Routing."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket" and scope.get("path") == PATH:
            return await serve(scope, receive, send)
        return await self.app(scope, receive, send)


async def _close(send, code):
    try:
        await send({"type": "websocket.close", "code": code})
    except Exception:  # noqa: BLE001
        pass


async def serve(scope, receive, send):
    if (await receive())["type"] != "websocket.connect":
        return
    try:
        on = await asyncio.to_thread(enabled)
        who = await asyncio.to_thread(_who, scope) if on else None
    except Exception:  # noqa: BLE001 - Datenbank kurz weg
        await send({"type": "websocket.accept"})
        await _close(send, C_RESTART)
        return
    await send({"type": "websocket.accept"})
    if not on:
        await _close(send, C_OFF)          # Stufe 2 aus: Client bleibt bei Stufe 1
        return
    if not who:
        await _close(send, C_AUTH)
        return
    uid, kind, check, origin = who
    if kind == "web" and not _origin_ok(scope):
        await _close(send, C_ORIGIN)
        return
    if sum(len(v) for v in _conns.values()) >= MAX_TOTAL:
        await _close(send, C_LIMIT)
        return
    conn = _Conn(None, uid, kind, check, origin)
    lst = _conns.setdefault(uid, [])
    while len(lst) >= MAX_PER_USER:
        lst.pop(0).kill(C_LIMIT)
    lst.append(conn)

    async def writer():
        while True:
            m = await conn.q.get()
            if m is None:
                return
            try:
                await send({"type": "websocket.send", "bytes": m})
            except Exception:  # noqa: BLE001 - Gegenstelle weg
                conn.closed = True
                return

    async def checker():
        while not conn.closed:
            await asyncio.sleep(CHECK_EVERY)
            try:
                ok = await asyncio.to_thread(check)
            except Exception:  # noqa: BLE001
                ok = True
            if not ok or not await asyncio.to_thread(enabled):
                for s in list(conn.subs.values()):
                    conn.send(frame(T_BYE, s.nid, {"reason": "auth" if not ok else "off"}))
                conn.kill(C_AUTH if not ok else C_OFF)
                return
            await recheck(uids={uid})

    wt = asyncio.create_task(writer())
    ct = asyncio.create_task(checker())
    rt = None
    try:
        conn.send(frame(T_HELLO, 0, {"proto": PROTO, "schema": {"model": model.MODEL, "ver": model.SCHEMA_VER, "hash": model.SCHEMA_HASH},
                                     "max": MSG_MAX}))

        async def reader():
            while not conn.closed:
                m = await receive()
                if m["type"] == "websocket.disconnect":
                    return
                b = m.get("bytes")
                if b is None:
                    conn.kill(C_PROTO)
                    return
                if len(b) > MSG_MAX + 64:
                    conn.kill(C_LIMIT)
                    return
                try:
                    k, nid, parts = parse(b)
                except ValueError:
                    conn.kill(C_PROTO)
                    return
                await _handle(conn, k, nid, parts)
        rt = asyncio.create_task(reader())
        await asyncio.wait({rt, wt}, return_when=asyncio.FIRST_COMPLETED)
    except Exception as ex:  # noqa: BLE001
        print(f"collab: Verbindung: {type(ex).__name__}: {ex}", flush=True)
    finally:
        conn.closed = True
        for tk in (rt, ct):
            if tk is not None:
                tk.cancel()
        if not wt.done():
            # Rest ausliefern (bye), dann schliessen
            try:
                conn.q.put_nowait(None)
            except asyncio.QueueFull:
                pass
            try:
                await asyncio.wait_for(wt, 2)
            except Exception:  # noqa: BLE001
                wt.cancel()
        for s in list(conn.subs.values()):
            _drop_sub(s)
        if conn in _conns.get(uid, []):
            _conns[uid].remove(conn)
            if not _conns[uid]:
                _conns.pop(uid, None)
        await _close(send, conn.code)


# =====================================================================================================================
# Start/Stopp, Aufraeumen
# =====================================================================================================================
def _housekeeping_db():
    with _db() as c, c:
        c.execute("DELETE FROM note_crdt WHERE used < ? AND updated <= mat", (time.time() - DROP_DAYS * 86400,))
        c.execute("DELETE FROM note_crdt_log WHERE note_id NOT IN (SELECT note_id FROM note_crdt)")
        stale = [r[0] for r in c.execute("SELECT note_id FROM note_crdt WHERE updated > mat").fetchall()]
    return stale


async def _housekeeping():
    first = True
    while True:
        try:
            if first:
                # nach einem Neustart: Seiten mit Live-Aenderungen, die noch nicht in note.body stehen, materialisieren
                first = False
                for nid in await asyncio.to_thread(_housekeeping_db):
                    if nid not in _rooms:
                        room = await asyncio.to_thread(_load_room, nid)
                        if room is not None and nid not in _rooms:
                            _rooms[nid] = room
                            await asyncio.to_thread(_materialize, room, True)
                            if not room.subs:
                                _rooms.pop(nid, None)
            for room in list(_rooms.values()):
                if room.n_log and (room.n_log >= COMPACT_N or time.time() - room.last >= COMPACT_IDLE):
                    def comp(r=room):
                        with r.lock, _db() as c, c:
                            _save_state(c, r, mat=0 if r.dirty_at else None)
                    await asyncio.to_thread(comp)
        except Exception as ex:  # noqa: BLE001
            print(f"collab: Aufraeumen: {type(ex).__name__}: {ex}", flush=True)
        await asyncio.sleep(5)


async def _daily():
    while True:
        await asyncio.sleep(86400)
        try:
            await asyncio.to_thread(_housekeeping_db)
        except Exception:  # noqa: BLE001
            pass


def start():
    if _S["loop"] is not None:
        return
    _S["loop"] = asyncio.get_running_loop()
    _S["tasks"] = [asyncio.create_task(_housekeeping()), asyncio.create_task(_daily())]


async def shutdown():
    """Beim Herunterfahren: offene Raeume materialisieren und verdichten, Verbindungen mit bye schliessen."""
    for lst in list(_conns.values()):
        for conn in list(lst):
            for s in list(conn.subs.values()):
                conn.send(frame(T_BYE, s.nid, {"reason": "restart"}))
            conn.kill(C_RESTART)
    for room in list(_rooms.values()):
        try:
            await asyncio.to_thread(_materialize, room, True)
            def comp(r=room):
                with r.lock, _db() as c, c:
                    _save_state(c, r, mat=0 if r.dirty_at else None)
            await asyncio.to_thread(comp)
        except Exception as ex:  # noqa: BLE001
            print(f"collab: Herunterfahren {room.nid}: {type(ex).__name__}: {ex}", flush=True)
    for tk in _S["tasks"]:
        tk.cancel()
    _S.update(loop=None, tasks=[])


def drop_note(c, nid):
    """Seite endgueltig geloescht bzw. Konto weg: CRDT-Zustand mit."""
    c.execute("DELETE FROM note_crdt_log WHERE note_id=?", (nid,))
    c.execute("DELETE FROM note_crdt WHERE note_id=?", (nid,))


def state_hash(nid):
    """Fuer Tests/Diagnose: Hash des Zustands im Speicher."""
    room = _rooms.get(nid)
    return hashlib.sha256(room.doc.get_update()).hexdigest()[:16] if room else None
