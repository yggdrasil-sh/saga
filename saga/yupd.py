"""Yjs-Updates (Kodierung v1, lib0) lesen und Werte darin umschreiben, ohne den Aufbau zu aendern (2.5, P3-E7 collab1).

Gebraucht fuer das Live-Mitschreiben ueber Servergrenzen (saga/fed_collab.py): Der Zustand einer Seite steht beim
Eigentuemer in SEINEN Kennungen (Seiten-IDs, /files/<id>, Erwaehnungen als lokaler Handle), das Heim zeigt sie in seinen
(negative IDs, /api/fed/files/..., eigene Handles). Alle diese Werte stehen in Inhalten, deren Laenge nicht vom Wert
abhaengt (Attribute von Y.XmlElement = ContentAny, Marken = ContentFormat, JSON/Einbettung) - sie lassen sich ersetzen,
ohne dass sich Uhren, Ursprungs-IDs oder Laengen aendern. Beide Seiten haben so denselben Aufbau (gleicher
Zustandsvektor, Positionen/Cursor bleiben gueltig), nur andere Werte. Text (ContentString) bleibt unberuehrt.

transform(update, fn, keys) -> (neue Bytes, offen)
    fn(wert, schluessel) -> neuer Wert (derselbe = unveraendert). schluessel: (Knotenname des Elternelements,
    Attributname) - bei Marken ("mark", Markenname); ueberschriebene Attribute (Eintrag mit Ursprung, ohne parent/
    parentSub in den Bytes) erben ihn ueber origin aus keys bzw. aus demselben Update - sonst None. offen: Anzahl
    Eintraege mit Inhalt ohne bekannten Schluessel (Luecke: das Update haengt an Unbekanntem).
    keys: Gedaechtnis je Seite (dict), wird um die Eintraege dieses Updates ergaenzt.
    Reject (aus fn) bricht ab. ValueError bei kaputtem Update.
Unveraenderte Inhalte werden byte-gleich kopiert; geaenderte ContentAny-Werte mit lib0 writeAny kodiert (Zahlen als
float64, wie Yjs/yrs sie lesen)."""
import json
import struct

KEYS_MAX = 200000


class Reject(Exception):
    """Wert laesst sich nicht abbilden (fn) - das Update wird nicht weitergereicht."""


class _R:
    __slots__ = ("b", "i")

    def __init__(self, b):
        self.b, self.i = b, 0

    def u8(self):
        if self.i >= len(self.b):
            raise ValueError("ende")
        x = self.b[self.i]
        self.i += 1
        return x

    def vu(self):
        n = s = 0
        while True:
            x = self.u8()
            n |= (x & 0x7F) << s
            s += 7
            if not x & 0x80:
                return n
            if s > 63:
                raise ValueError("varuint")

    def vi(self):
        x = self.u8()
        n = x & 0x3F
        neg = bool(x & 0x40)
        s = 6
        while x & 0x80:
            x = self.u8()
            n |= (x & 0x7F) << s
            s += 7
            if s > 63:
                raise ValueError("varint")
        return -n if neg else n

    def raw(self, n):
        if n < 0 or self.i + n > len(self.b):
            raise ValueError("laenge")
        out = self.b[self.i:self.i + n]
        self.i += n
        return out

    def vs(self):
        return self.raw(self.vu()).decode("utf-8")

    def any(self, depth=0):
        if depth > 64:
            raise ValueError("tief")
        t = self.u8()
        if t in (126, 127):
            return None
        if t == 125:
            return self.vi()
        if t == 124:
            return struct.unpack(">f", self.raw(4))[0]
        if t == 123:
            return struct.unpack(">d", self.raw(8))[0]
        if t == 122:
            return struct.unpack(">q", self.raw(8))[0]
        if t == 121:
            return False
        if t == 120:
            return True
        if t == 119:
            return self.vs()
        if t == 118:
            out = {}
            for _ in range(self.vu()):
                k = self.vs()
                out[k] = self.any(depth + 1)
            return out
        if t == 117:
            return [self.any(depth + 1) for _ in range(self.vu())]
        if t == 116:
            return bytes(self.raw(self.vu()))
        raise ValueError(f"any {t}")


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


def _vs(s):
    b = s.encode("utf-8")
    return _vu(len(b)) + b


def _any(v, depth=0):
    if depth > 64:
        raise ValueError("tief")
    if v is None:
        return b"\x7e"
    if v is True:
        return b"\x78"
    if v is False:
        return b"\x79"
    if isinstance(v, (int, float)):
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            raise ValueError("zahl")
        return b"\x7b" + struct.pack(">d", f)
    if isinstance(v, str):
        return b"\x77" + _vs(v)
    if isinstance(v, dict):
        return b"\x76" + _vu(len(v)) + b"".join(_vs(str(k)) + _any(x, depth + 1) for k, x in v.items())
    if isinstance(v, (list, tuple)):
        return b"\x75" + _vu(len(v)) + b"".join(_any(x, depth + 1) for x in v)
    if isinstance(v, (bytes, bytearray)):
        return b"\x74" + _vu(len(v)) + bytes(v)
    raise ValueError("typ")


def same(a, b):
    """Gleich im Sinne von JSON (1 == 1.0, aber True != 1)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b


def _parse(b):
    """-> (bytes, gruppen [(n, client, clock, erster_index)], eintraege, rest_ab). Ein Eintrag: Kopf/Inhalt-Grenzen in b,
    ref, id, origin, rorigin, parent (("root", name) | ("id", (client, clock)) | None), sub, ln, bei Typen tag."""
    r = _R(bytes(b))
    groups, items = [], []
    for _ in range(r.vu()):
        n, client, clock = r.vu(), r.vu(), r.vu()
        groups.append((n, client, clock, len(items)))
        for _ in range(n):
            start = r.i
            info = r.u8()
            ref = info & 0x1F
            if ref in (0, 10):
                ln = r.vu()
                items.append({"raw": (start, r.i), "ref": ref, "ln": ln})
                clock += ln
                continue
            origin = (r.vu(), r.vu()) if info & 0x80 else None
            rorigin = (r.vu(), r.vu()) if info & 0x40 else None
            parent = sub = None
            if not info & 0xC0:
                if r.vu() == 1:
                    parent = ("root", r.vs())
                else:
                    parent = ("id", (r.vu(), r.vu()))
                if info & 0x20:
                    sub = r.vs()
            it = {"head": (start, r.i), "ref": ref, "id": (client, clock), "origin": origin, "rorigin": rorigin, "parent": parent,
                  "sub": sub}
            if ref == 1:
                ln = r.vu()
            elif ref == 3:
                r.raw(r.vu())
                ln = 1
            elif ref == 4:
                ln = len(r.vs().encode("utf-16-le")) // 2
            elif ref == 7:
                tref = r.vu()
                it["tag"] = r.vs() if tref in (3, 5) else f"#{tref}"
                ln = 1
            elif ref == 6:
                it["fk"] = r.vs()
                it["vpos"] = r.i
                r.vs()
                ln = 1
            elif ref == 5:
                it["vpos"] = r.i
                r.vs()
                ln = 1
            elif ref == 2:
                ln = r.vu()
                it["vpos"] = r.i
                for _j in range(ln):
                    r.vs()
            elif ref == 8:
                ln = r.vu()
                it["vpos"] = r.i
                for _j in range(ln):
                    r.any()
            elif ref == 9:
                r.vs()
                r.any()
                ln = 1
            else:
                raise ValueError(f"inhalt {ref}")
            it["end"], it["ln"] = r.i, ln
            items.append(it)
            clock += ln
    return r.b, groups, items, r.i


def _json_val(s):
    if s == "undefined":
        return None, False
    return json.loads(s), True


def _put(local, it):
    """Schluessel fuer alle Uhren eines Eintrags merken (zusammengefasste geloeschte Eintraege sind laenger als 1) -
    nur fuer Map-Eintraege (Attribute); Text braucht keinen."""
    if it["key"][1] is None:
        return
    c, k = it["id"]
    for i in range(min(it["ln"], 1024)):
        local[(c, k + i)] = it["key"]


def _resolve(items, keys):
    """(Eltern-Tag, Schluessel) je Eintrag: explizit (parent/parentSub) bzw. ueber origin/rightOrigin (Ketten auch innerhalb
    des Updates). Gedaechtnis keys: {(client, clock): (tag, sub)} und {("T", client, clock): tag} fuer Typen.
    -> Anzahl Eintraege mit Inhalt, die einen Schluessel braeuchten und keinen haben."""
    tags = {("T",) + it["id"]: it["tag"] for it in items if it.get("tag") is not None}

    def tag_of(pid):
        return tags.get(("T",) + pid) or keys.get(("T",) + pid)
    local = {}
    todo = []
    for it in items:
        if "raw" in it:
            continue
        par = it["parent"]
        if par is not None:
            ptag = ("#" + par[1]) if par[0] == "root" else tag_of(par[1])
            it["key"] = (ptag, it["sub"])
            _put(local, it)
        else:
            todo.append(it)
    for _ in range(64):
        left = []
        for it in todo:
            k = None
            for o in (it["origin"], it["rorigin"]):
                if o is not None:
                    k = local.get(o) or keys.get(o)
                    if k is not None:
                        break
            if k is None:
                left.append(it)
            else:
                it["key"] = k
                _put(local, it)
        if len(left) == len(todo):
            break
        todo = left
    keys.update(local)
    keys.update(tags)
    if len(keys) > KEYS_MAX:
        for k in list(keys)[:KEYS_MAX // 2]:
            keys.pop(k, None)
    return sum(1 for it in todo if it["ref"] in (2, 5, 8))


def transform(update, fn, keys=None):
    """Siehe Modul-Doku. -> (neue Bytes, Anzahl ohne Schluessel)."""
    b, groups, items, rest = _parse(update)
    keys = {} if keys is None else keys
    open_n = _resolve(items, keys)
    out = bytearray(_vu(len(groups)))
    for n, client, clock, first in groups:
        out += _vu(n) + _vu(client) + _vu(clock)
        for it in items[first:first + n]:
            if "raw" in it:
                out += b[it["raw"][0]:it["raw"][1]]
                continue
            ref, key = it["ref"], it.get("key")
            hs = it["head"][0]
            if ref not in (2, 5, 6, 8):
                out += b[hs:it["end"]]
                continue
            r = _R(b)
            r.i = it["vpos"]
            if ref == 6:
                s0 = r.i
                val, has = _json_val(r.vs())
                nv = fn(val, ("mark", it["fk"])) if has and val is not None else val
                out += b[hs:s0] + (b[s0:r.i] if not has or same(nv, val) else _vs(json.dumps(nv, ensure_ascii=False)))
            elif ref == 5:
                s0 = r.i
                val, has = _json_val(r.vs())
                nv = fn(val, key) if has else val
                out += b[hs:s0] + (b[s0:r.i] if not has or same(nv, val) else _vs(json.dumps(nv, ensure_ascii=False)))
            elif ref == 2:
                parts = []
                for _j in range(it["ln"]):
                    s0 = r.i
                    val, has = _json_val(r.vs())
                    nv = fn(val, key) if has else val
                    parts.append(b[s0:r.i] if not has or same(nv, val) else _vs(json.dumps(nv, ensure_ascii=False)))
                out += b[hs:it["vpos"]] + b"".join(parts)
            else:
                parts = []
                for _j in range(it["ln"]):
                    s0 = r.i
                    val = r.any()
                    nv = fn(val, key)
                    parts.append(b[s0:r.i] if same(nv, val) else _any(nv))
                out += b[hs:it["vpos"]] + b"".join(parts)
    out += b[rest:]
    return bytes(out), open_n


def key_scan(update, keys):
    """Nur Schluessel merken (z. B. Zustand beim Laden)."""
    transform(update, lambda v, k: v, keys)
