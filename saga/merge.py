"""Dreiwege-Zusammenfuehren von Seiten (2.5, Paket 3 Stufe 1) - reine Funktionen, ohne Datenbank.

merge_doc(base, mine, theirs) fuehrt zwei Fassungen einer Seite zusammen, die beide auf base beruhen: mine = was der
Client gerade speichert, theirs = der aktuelle Stand auf dem Server. Grundlage sind eindeutige Block-IDs
(notes.fix_block_ids). Die Regeln (Vertrag, docs/APP-API.md "Gleichzeitig bearbeiten", Testvektoren tests/vectors/merge.json):

  Struktur  - Baum aus Listen: Wurzel, Spalten (columns.data.cols[i].blocks), Container (toggle/synced data.blocks).
            - Block nur auf einer Seite neu: bleibt; auf beiden Seiten neu: beide, am Vorgaenger verankert, "theirs" zuerst.
            - einseitig geloescht, andere Seite unveraendert: geloescht; andere Seite geaendert: Aenderung gewinnt (restored).
            - Reihenfolge: einseitig geaendert -> uebernehmen; beidseitig -> "theirs", eigene Verschiebungen dort, wo sie
              nicht kollidieren (LCS ueber Block-IDs). Verschieben zwischen Listen: Position von "mine", wenn beide.
  Inhalt    - je Feld von data/tunes: nur eine Seite geaendert -> diese; beide gleich -> einmal.
            - Textfelder (TEXT_KEYS, Inline-HTML) beidseitig geaendert: Dreiwege-Textmerge auf (Zeichen, Marken);
              Verweise, Datum, Erwaehnung und Inline-Mathe sind je eine Einheit (P6-0). Ueberschneidung -> Konfliktblock:
              "theirs" bleibt an der Stelle, "mine" kommt als neuer Block direkt darunter mit tunes.hkconflict.
            - Listenpunkte/Tabellenzeilen: als Folge zusammengefuehrt, je Punkt/Zeile wieder Textmerge.
            - andere Felder beidseitig verschieden: "mine" gewinnt, Block in info["lost"] (Server sichert "theirs" als Version).
  Titel     - merge_title: Textmerge, Ueberschneidung -> "mine" (lost).

Inline-Mathe (code.hk-math) und der Mathe-Block (tex) werden nie zeichenweise gemischt (atomar)."""
import json
import re
import unicodedata
from difflib import SequenceMatcher
from html import escape
from html.parser import HTMLParser

CONTAINERS = ("toggle", "synced")
TEXT_KEYS = ("text", "title", "caption", "content", "message")
PLAIN_TEXT_KEYS = ("code",)                # Klartext (kein HTML) - zeichenweise
ATOMIC_KEYS = ("tex",)                     # nie zeichenweise mischen
# Bloecke, bei denen eine Text-Ueberschneidung einen Konfliktblock erzeugt (sonst: "mine" gewinnt, lost)
CONFLICT_TYPES = ("paragraph", "header", "list", "checklist", "quote", "callout", "toggle", "table", "code", "math", "synced")
WORD_LEVEL = 5000                          # laengere Texte: Wortebene (schneller, weniger Zufallstreffer)
_MISSING = object()


class _Conflict(Exception):
    """Textmerge: beide Seiten haben dieselbe Stelle geaendert."""


# =====================================================================================================
# Text: Inline-HTML <-> Folge von Einheiten (Zeichen mit Marken bzw. ganze Verweise/Daten/Erwaehnungen)
# =====================================================================================================
_ATOMIC_TAGS = ("a", "time")


def _atomic(tag, attrs):
    cls = (dict(attrs).get("class") or "").split()
    return tag in _ATOMIC_TAGS or (tag == "span" and "hk-mention" in cls) or (tag == "code" and "hk-math" in cls)


def _start(tag, attrs):
    return "<" + tag + "".join(f' {k}="{escape(v or "", quote=True)}"' for k, v in attrs) + ">"


A = "\x00"                                 # Kennung atomarer Einheiten (NUL kommt in Eingaben nie vor, core.clean_json)


class _Tok(HTMLParser):
    """HTML (vom Sanitizer, also wohlgeformt) -> [(einheit, marken)]: einheit = Graphem bzw. A + ganzes atomares Element
    als HTML (auch <br>); marken = Tupel der offenen Start-Tags (aussen zuerst)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.toks, self.marks, self.atom, self.depth = [], [], None, 0

    def handle_starttag(self, tag, attrs):
        if self.atom is not None:
            if tag == "br":
                self.atom.append("<br>")
                return
            self.atom.append(_start(tag, attrs))
            self.depth += tag == self.atag
            return
        if tag == "br":
            self.toks.append((A + "<br>", tuple(m[1] for m in self.marks)))
            return
        if _atomic(tag, attrs):
            self.atom, self.atag, self.depth = [_start(tag, attrs)], tag, 1
            return
        self.marks.append((tag, _start(tag, attrs)))

    def handle_endtag(self, tag):
        if self.atom is not None:
            if tag == "br":
                return
            self.atom.append(f"</{tag}>")
            if tag == self.atag:
                self.depth -= 1
                if self.depth == 0:
                    self.toks.append((A + "".join(self.atom), tuple(m[1] for m in self.marks)))
                    self.atom = None
            return
        for i in range(len(self.marks) - 1, -1, -1):
            if self.marks[i][0] == tag:
                del self.marks[i]
                break

    def handle_data(self, data):
        if self.atom is not None:
            self.atom.append(escape(data, quote=False))
            return
        mk = tuple(m[1] for m in self.marks)
        self.toks.extend((g, mk) for g in _graphemes(data))

    def close(self):
        super().close()
        if self.atom is not None:              # nicht geschlossen (sollte nach dem Sanitizer nicht vorkommen)
            self.atom.append(f"</{self.atag}>")
            self.toks.append((A + "".join(self.atom), tuple(m[1] for m in self.marks)))
            self.atom = None


def _joins(ch):
    o = ord(ch)
    return (unicodedata.combining(ch) or unicodedata.category(ch) in ("Mn", "Me") or 0xFE00 <= o <= 0xFE0F
            or 0x1F3FB <= o <= 0x1F3FF or 0xE0020 <= o <= 0xE007F or o == 0x200D)


def _graphemes(s):
    """Grob in Graphem-Gruppen teilen: Zeichen + kombinierende Zeichen (auch Thai), Varianten, Hautfarben, ZWJ-Folgen,
    Flaggen aus zwei Regionalzeichen - damit ein Merge nie mitten in einem sichtbaren Zeichen trennt."""
    out = []
    for ch in s:
        if out and (_joins(ch) or out[-1].endswith("‍")
                    or (0x1F1E6 <= ord(ch) <= 0x1F1FF and len(out[-1]) == 1 and 0x1F1E6 <= ord(out[-1]) <= 0x1F1FF)):
            out[-1] += ch
        else:
            out.append(ch)
    return out


def tokens(html):
    p = _Tok()
    p.feed(html or "")
    p.close()
    return p.toks


def _end_tag(start):
    return "</" + re.match(r"<([a-z0-9]+)", start).group(1) + ">"


def render(toks):
    """[(einheit, marken)] -> HTML. Marken als Stapel: gemeinsamer Anfang bleibt offen, der Rest wird geschlossen/geoeffnet."""
    out, cur = [], ()
    for unit, mk in toks:
        k = 0
        while k < len(cur) and k < len(mk) and cur[k] == mk[k]:
            k += 1
        for s in reversed(cur[k:]):
            out.append(_end_tag(s))
        out.extend(mk[k:])
        cur = mk
        out.append(unit[1:] if unit.startswith(A) else escape(unit, quote=False))
    for s in reversed(cur):
        out.append(_end_tag(s))
    return "".join(out)


def _words(toks):
    """Wortebene: aufeinanderfolgende Wortzeichen gleicher Marken zu einer Einheit zusammenfassen."""
    out = []
    for u, mk in toks:
        w = not u.startswith(A) and u[:1].isalnum()
        if out and w and out[-1][2] and out[-1][1] == mk:
            out[-1] = (out[-1][0] + u, mk, True)
        else:
            out.append((u, mk, w))
    return [(u, mk) for u, mk, _ in out]


# =====================================================================================================
# Dreiwege-Merge auf Folgen
# =====================================================================================================
def _hunks(a, b):
    """Aenderungen von a nach b als [(von, bis, ersatz)] in a-Koordinaten."""
    sm = SequenceMatcher(None, a, b, autojunk=False)
    return [(i1, i2, b[j1:j2]) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal"]


def _overlap(h, k):
    (s1, e1, _), (s2, e2, _) = h, k
    if s1 == e1 and s2 == e2:
        return s1 == s2                       # zwei Einfuegungen an derselben Stelle
    if s1 == e1:
        return s2 < s1 < e2
    if s2 == e2:
        return s1 < s2 < e1
    return s1 < e2 and s2 < e1


def merge_seq(base, mine, theirs, sub=None, both_inserts=False):
    """Dreiwege-Merge zweier Folgen. sub(b, m, t) -> Ersatz fuer sich ueberschneidende Aenderungen gleicher Laenge
    (Element fuer Element, z. B. Listenpunkte) oder _Conflict. both_inserts: reine Einfuegungen beider Seiten an
    derselben Stelle -> "theirs", dann "mine" (Listenpunkte; im Text waere das ein Konflikt). -> neue Folge; _Conflict."""
    hm, ht = _hunks(base, mine), _hunks(base, theirs)
    allh = sorted([(h, 0) for h in hm] + [(h, 1) for h in ht], key=lambda x: (x[0][0], x[0][1]))
    out, pos, i = [], 0, 0
    while i < len(allh):
        cl = [allh[i]]
        lo, hi = allh[i][0][0], allh[i][0][1]
        j = i + 1
        while j < len(allh) and any(_overlap(allh[j][0], c[0]) for c in cl):
            cl.append(allh[j]); hi = max(hi, allh[j][0][1]); j += 1
        out.extend(base[pos:lo])
        sides = {s for _, s in cl}
        if len(sides) == 1:
            for (s, e, rep), _ in cl:            # nur eine Seite: (nicht ueberlappende) Aenderungen der Reihe nach
                out.extend(rep)
            pos = hi
        else:
            m_res = _apply(base, lo, hi, [h for h, s in cl if s == 0])
            t_res = _apply(base, lo, hi, [h for h, s in cl if s == 1])
            if m_res == t_res:
                out.extend(m_res)
            elif both_inserts and lo == hi:
                out.extend(t_res + m_res)
            elif sub is not None and len(m_res) == len(t_res) == hi - lo:
                out.extend(sub(b, m, t) for b, m, t in zip(base[lo:hi], m_res, t_res))
            else:
                raise _Conflict()
            pos = hi
        i = j
    out.extend(base[pos:])
    return out


def _apply(base, lo, hi, hs):
    """Aenderungen einer Seite auf base[lo:hi] anwenden."""
    out, p = [], lo
    for s, e, rep in sorted(hs, key=lambda h: (h[0], h[1])):
        out.extend(base[p:s]); out.extend(rep); p = e
    out.extend(base[p:hi])
    return out


def merge_text(b, m, t, html=True):
    """Dreiwege-Textmerge (Inline-HTML bzw. Klartext). -> Ergebnis oder _Conflict."""
    if m == b or m == t:
        return t
    if t == b:
        return m
    if not html:
        tb, tm, tt = _graphemes(b), _graphemes(m), _graphemes(t)
        return "".join(merge_seq(tb, tm, tt))
    tb, tm, tt = tokens(b), tokens(m), tokens(t)
    if max(len(tb), len(tm), len(tt)) > WORD_LEVEL:
        tb, tm, tt = _words(tb), _words(tm), _words(tt)
    return render(merge_seq(tb, tm, tt))


def merge_title(b, m, t):
    """Titel (Klartext): -> (titel, lost?)."""
    try:
        return merge_text(b or "", m or "", t or "", html=False), False
    except _Conflict:
        return m, True


# =====================================================================================================
# Werte (data/tunes): feldweise
# =====================================================================================================
def _canon(v):
    return json.dumps(v, sort_keys=True, ensure_ascii=False)


def _eq(a, b):
    return a is b or (a is not _MISSING and b is not _MISSING and _canon(a) == _canon(b))


class _Lost(Exception):
    pass


def merge_value(b, m, t, key=None, flags=None):
    """Ein Feld zusammenfuehren. flags["lost"] = True, wenn ein Nicht-Textfeld beidseitig verschieden geaendert wurde
    ("mine" gewinnt). _Conflict bei Text-Ueberschneidung."""
    if _eq(m, b):
        return t
    if _eq(t, b) or _eq(m, t):
        return m
    if m is _MISSING or t is _MISSING:          # eine Seite hat das Feld entfernt, die andere geaendert
        if flags is not None:
            flags["lost"] = True
        return m
    if key in ATOMIC_KEYS:
        raise _Conflict()
    if key in TEXT_KEYS and all(isinstance(x, str) for x in (m, t)) and (b is _MISSING or isinstance(b, str)):
        return merge_text("" if b is _MISSING else b, m, t)
    if key in PLAIN_TEXT_KEYS and all(isinstance(x, str) for x in (m, t)) and (b is _MISSING or isinstance(b, str)):
        return merge_text("" if b is _MISSING else b, m, t, html=False)
    if isinstance(m, dict) and isinstance(t, dict) and (b is _MISSING or isinstance(b, dict)):
        return merge_dict({} if b is _MISSING else b, m, t, flags)
    if key in ("items", "content") and isinstance(m, list) and isinstance(t, list) and (b is _MISSING or isinstance(b, list)):
        return _merge_list_value([] if b is _MISSING else b, m, t, key, flags)
    if key == "cols" and isinstance(m, list) and isinstance(t, list) and isinstance(b, list) and len(b) == len(m) == len(t) \
            and all(isinstance(x, dict) for x in b + m + t):
        return [merge_dict(xb, xm, xt, flags) for xb, xm, xt in zip(b, m, t)]     # Spalten (ohne Inhalt): je Spalte
    if flags is not None:
        flags["lost"] = True
    return m


def merge_dict(b, m, t, flags=None, skip=()):
    out = {}
    for k in list(dict.fromkeys(list(t) + list(m))):
        if k in skip:
            continue
        v = merge_value(b.get(k, _MISSING), m.get(k, _MISSING), t.get(k, _MISSING), k, flags)
        if v is not _MISSING:
            out[k] = v
    return out


def _merge_list_value(b, m, t, key, flags):
    """Listenpunkte (list.items: [{content, meta, items}]) bzw. Tabellenzeilen (table.content: [[zelle]]) als Folge;
    beidseitig geaenderte Punkte/Zeilen gleicher Anzahl wieder einzeln (Text je Punkt/Zelle)."""
    enc = lambda x: _canon(x)                  # noqa: E731
    bb, mm, tt = [enc(x) for x in b], [enc(x) for x in m], [enc(x) for x in t]

    def sub(xb, xm, xt):
        vb, vm, vt = json.loads(xb), json.loads(xm), json.loads(xt)
        if isinstance(vm, list) and isinstance(vt, list) and isinstance(vb, list) and len(vb) == len(vm) == len(vt):
            return enc([merge_value(cb, cm, ct, "content", flags) for cb, cm, ct in zip(vb, vm, vt)])     # Tabellenzellen
        if isinstance(vm, dict) and isinstance(vt, dict) and isinstance(vb, dict):
            return enc(merge_dict(vb, vm, vt, flags))
        if isinstance(vm, str) and isinstance(vt, str) and isinstance(vb, str):
            return enc(merge_text(vb, vm, vt))
        raise _Conflict()
    return [json.loads(x) for x in merge_seq(bb, mm, tt, sub, both_inserts=True)]


# =====================================================================================================
# Dokument: Baum aus Listen
# =====================================================================================================
def _flat(doc):
    """Dokument -> (nodes {id: eigener Block ohne Kinder}, lists {schluessel: [ids]}, parent {id: schluessel},
    has_children {id}). Schluessel: "" = Wurzel, "<id>/c<i>" = Spalte i, "<id>/b" = Container-Inhalt."""
    nodes, lists, parent = {}, {"": []}, {}

    def walk(lst, key, depth):
        if depth > 10 or not isinstance(lst, list):
            return
        for b in lst:
            if not isinstance(b, dict) or not isinstance(b.get("id"), str) or b["id"] in nodes:
                continue
            bid, d = b["id"], b.get("data") if isinstance(b.get("data"), dict) else {}
            own = {k: v for k, v in b.items() if k != "data"}
            od = dict(d)
            if b.get("type") == "columns" and isinstance(d.get("cols"), list):
                od["cols"] = [{k: v for k, v in c.items() if k != "blocks"} if isinstance(c, dict) else {} for c in d["cols"]]
                for i, c in enumerate(d["cols"]):
                    lists[f"{bid}/c{i}"] = []
                    walk(c.get("blocks") if isinstance(c, dict) else None, f"{bid}/c{i}", depth + 1)
            elif b.get("type") in CONTAINERS and isinstance(d.get("blocks"), list):
                od["blocks"] = True                 # Platzhalter: Inhalt steht in lists[<id>/b]
                lists[f"{bid}/b"] = []
                walk(d["blocks"], f"{bid}/b", depth + 1)
            own["data"] = od
            nodes[bid] = own
            parent[bid] = key
            lists[key].append(bid)

    walk(doc.get("blocks") if isinstance(doc, dict) else None, "", 0)
    return nodes, lists, parent


def _stable(base, other):
    """IDs aus other, die (gegenueber base) nicht verschoben wurden - laengste gemeinsame Teilfolge."""
    common = set(base) & set(other)
    a, b = [x for x in base if x in common], [x for x in other if x in common]
    sm = SequenceMatcher(None, a, b, autojunk=False)
    out = set()
    for i, _, n in sm.get_matching_blocks():
        out.update(a[i:i + n])
    return out


def _order(bl, ml, tl, members, is_new_theirs):
    """Reihenfolge einer Liste aus base/mine/theirs fuer die Bloecke members (s. Modulkopf)."""
    m_moved = {x for x in ml if x not in _stable(bl, ml)}
    t_moved = {x for x in tl if x not in _stable(bl, tl)}
    order = [x for x in tl if x in members and not (x in m_moved and x not in t_moved and x in ml)]
    placed = set(order)
    for k, x in enumerate(ml):
        if x not in members or x in placed:
            continue
        # Anker aus "mine": Vorgaenger und Nachfolger, die schon stehen. Passt beides, direkt vor den Nachfolger (neue
        # Bloecke von "theirs" am selben Anker stehen damit zuerst); sonst hinter den Vorgaenger.
        pre = next((y for y in reversed(ml[:k]) if y in placed), None)
        suc = next((y for y in ml[k + 1:] if y in placed), None)
        lo = order.index(pre) + 1 if pre is not None else 0
        hi = order.index(suc) if suc is not None else len(order)
        if lo <= hi:
            pos = hi
        else:
            pos = lo
            while pos < len(order) and is_new_theirs(order[pos]):  # gleicher Anker: "theirs" zuerst
                pos += 1
        order.insert(pos, x)
        placed.add(x)
    order.extend(x for x in members if x not in placed)             # (Absicherung)
    return order


def merge_doc(base, mine, theirs, bases=None, by=None, now=None):
    """-> (doc, info). base/mine/theirs: {"blocks": [...]} mit eindeutigen Block-IDs. bases: {id: Block} - andere Basis
    fuer einzelne Bloecke (Client hat waehrend des Speicherns weitergeschrieben, APP-API "bases"). by: {"by", "name"} fuer
    Konfliktbloecke. info: conflicts (IDs der neuen Konfliktbloecke), restored, lost."""
    nb, lb, pb = _flat(base)
    nm, lm, pm = _flat(mine)
    nt, lt, pt = _flat(theirs)
    for bid, blk in (bases or {}).items():         # Block-Basis aus "bases": nur der eigene Inhalt (ohne Kinder)
        if isinstance(blk, dict) and bid in nm:
            nb[bid] = _flat({"blocks": [dict(blk, id=bid)]})[0].get(bid, nb.get(bid))
    info = {"conflicts": [], "restored": [], "lost": []}

    def lists_of(lists, bid):
        return {k[len(bid):]: v for k, v in lists.items() if k.startswith(bid + "/")}

    def changed(side_nodes, side_lists, bid):
        if not _eq(side_nodes.get(bid, _MISSING), nb.get(bid, _MISSING)):
            return True
        return _canon(lists_of(side_lists, bid)) != _canon(lists_of(lb, bid))

    # 1. Welche Bloecke bleiben, und mit welchem Inhalt?
    keep, out_nodes, extra = {}, {}, {}
    for bid in dict.fromkeys(list(nt) + list(nm) + list(nb)):
        inb, inm, intt = bid in nb, bid in nm, bid in nt
        if inb and not inm and not intt:
            continue
        if inb and not inm:                        # in mine geloescht
            if changed(nt, lt, bid):
                info["restored"].append(bid); keep[bid] = "t"; out_nodes[bid] = nt[bid]
            continue
        if inb and not intt:                       # in theirs geloescht
            if changed(nm, lm, bid):
                info["restored"].append(bid); keep[bid] = "m"; out_nodes[bid] = nm[bid]
            continue
        if not inm:
            keep[bid] = "t"; out_nodes[bid] = nt[bid]; continue
        if not intt:
            keep[bid] = "m"; out_nodes[bid] = nm[bid]; continue
        keep[bid] = "b"
        b0 = nb.get(bid, {"type": nt[bid].get("type"), "data": {}})
        out_nodes[bid], copy = _merge_block(b0, nm[bid], nt[bid], info)
        if copy is not None:
            extra[bid] = copy

    # Gerettete Bloecke (einseitig geloescht, andere Seite geaendert): ihre Container/Spalten-Bloecke mitretten
    for bid in list(info["restored"]):
        side_p = pt if keep[bid] == "t" else pm
        side_n = nt if keep[bid] == "t" else nm
        key = side_p.get(bid, "")
        while key:
            owner = key.rpartition("/")[0]
            if owner in keep or owner not in side_n:
                break
            keep[owner] = keep[bid]; out_nodes[owner] = side_n[owner]; info["restored"].append(owner)
            key = side_p.get(owner, "")

    # 2. Wo steht jeder Block? (eigene Liste; Container-/Spalten-Listen gibt es nur, wenn ihr Block bleibt)
    def place(bid):
        a, m, t = pb.get(bid), pm.get(bid), pt.get(bid)
        if keep[bid] == "m":
            return m
        if keep[bid] == "t":
            return t
        return t if m == a else m if t == a else m

    where = {bid: place(bid) for bid in keep}
    ncols = {bid: len(n["data"].get("cols") or []) for bid, n in out_nodes.items() if n.get("type") == "columns"}

    def valid(key):
        if key == "":
            return True
        owner, _, kind = key.rpartition("/")
        if owner not in out_nodes:
            return False
        n = out_nodes[owner]
        if kind == "b":
            return n.get("type") in CONTAINERS and n["data"].get("blocks") is True
        return n.get("type") == "columns" and kind.startswith("c") and kind[1:].isdigit()

    for bid, key in list(where.items()):           # Ziel-Liste weg: an die Stelle des Containers bzw. letzte Spalte
        seen = set()
        while not valid(key) and key not in seen:
            seen.add(key)
            owner = key.rpartition("/")[0]
            key = where.get(owner, "")
        if key.startswith(tuple(f"{o}/c" for o in ncols)):
            owner, _, kind = key.rpartition("/")
            if int(kind[1:]) >= ncols[owner]:
                key = f"{owner}/c{max(0, ncols[owner] - 1)}"
        where[bid] = key if valid(key) else ""

    for bid in list(where):                        # Zyklus (mine: a in b, theirs: b in a): sonst haengt nichts an der Wurzel
        seen, x = set(), bid
        while where.get(x, "") and x not in seen:
            seen.add(x)
            x = where[x].rpartition("/")[0]
            if x == bid:                           # bid liegt (mittelbar) in sich selbst: in die Wurzel
                where[bid] = ""
                break

    # 3. Reihenfolge je Liste
    members = {}
    for bid, key in where.items():
        members.setdefault(key, []).append(bid)
    order = {}
    for key, mem in members.items():
        ms = set(mem)
        order[key] = _order(lb.get(key, []), lm.get(key, []), lt.get(key, []), ms,
                            lambda x: x not in nb and x not in nm)

    # 4. Zusammensetzen (Konfliktbloecke direkt unter dem Original)
    stamp = {k: v for k, v in (by or {}).items() if k in ("by", "name") and v}
    if now is not None:
        stamp["at"] = round(float(now), 3)

    def build(key, depth=0):
        out = []
        for bid in order.get(key, []):
            n = json.loads(json.dumps(out_nodes[bid]))
            d = n.setdefault("data", {})
            if n.get("type") == "columns" and isinstance(d.get("cols"), list):
                d["cols"] = [dict(c if isinstance(c, dict) else {}, blocks=build(f"{bid}/c{i}", depth + 1)) for i, c in enumerate(d["cols"])]
            elif d.get("blocks") is True:
                d["blocks"] = build(f"{bid}/b", depth + 1)
            out.append(n)
            if bid in extra:
                cp = extra[bid]
                cp.setdefault("tunes", {})["hkconflict"] = dict(stamp, of=bid)
                out.append(cp)
                info["conflicts"].append(cp["id"])
        return out

    used = set(out_nodes)

    for bid, cp in extra.items():                  # neue, eindeutige ID fuer den Konfliktblock
        k, nid = 0, f"{bid[:30]}-k"
        while nid in used:
            k += 1
            nid = f"{bid[:30]}-k{k}"
        used.add(nid)
        cp["id"] = nid
        if (cp.get("data") or {}).get("blocks") is True:
            cp["data"]["blocks"] = []                  # Inhalt eines Containers bleibt beim Original
        if cp.get("type") == "columns":
            cp["data"]["cols"] = [dict(c, blocks=[]) for c in cp["data"].get("cols") or []]

    doc = {k: v for k, v in (theirs if isinstance(theirs, dict) else {}).items() if k != "blocks"}
    if isinstance(mine, dict):
        doc.update({k: v for k, v in mine.items() if k in ("time", "version")})
    doc["blocks"] = build("")
    return doc, info


def _merge_block(b, m, t, info):
    """Eigener Inhalt eines Blocks (ohne Kinder) -> (block, konfliktkopie|None)."""
    if _eq(m, b):
        return t, None
    if _eq(t, b) or _eq(m, t):
        return m, None
    bid = t.get("id") or m.get("id")
    flags = {}
    try:
        typ = merge_value(b.get("type", _MISSING), m.get("type", _MISSING), t.get("type", _MISSING), "type", None)
        if not _eq(m.get("type"), t.get("type")) and not _eq(m.get("type"), b.get("type")) and not _eq(t.get("type"), b.get("type")):
            raise _Conflict()
        out = {"id": bid, "type": typ}
        out["data"] = merge_dict(b.get("data") or {}, m.get("data") or {}, t.get("data") or {}, flags)
        tn = merge_value(b.get("tunes", _MISSING), m.get("tunes", _MISSING), t.get("tunes", _MISSING), "tunes", flags)
        if isinstance(tn, dict) and tn:
            out["tunes"] = tn
        for k in m:                                # weitere Schluessel (selten): wie Felder
            if k not in ("id", "type", "data", "tunes"):
                v = merge_value(b.get(k, _MISSING), m.get(k, _MISSING), t.get(k, _MISSING), k, flags)
                if v is not _MISSING:
                    out[k] = v
        if flags.get("lost"):
            info["lost"].append(bid)
        return out, None
    except _Conflict:
        if (t.get("type") or m.get("type")) in CONFLICT_TYPES:
            cp = json.loads(json.dumps(m))
            tn = cp.get("tunes") if isinstance(cp.get("tunes"), dict) else {}
            tn.pop("hkconflict", None)
            cp["tunes"] = tn
            return t, cp
        info["lost"].append(bid)
        return m, None
