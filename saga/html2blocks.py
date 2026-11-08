"""HTML (CKEditor 5, wie Trilium es speichert und exportiert) -> Editor.js-Bloecke (2.5, Trilium-Import). Nur
Standardbibliothek; die Ausgabe geht wie bei md2blocks immer noch durch pages.clean_blocks (Positivliste).

Bloecke: h1-h6 -> header (Ebene bleibt), p -> paragraph (Bilder darin werden eigene Bild-Bloecke an ihrer Stelle),
ul/ol (verschachtelt) -> list, ul.todo-list -> checklist, pre>code.language-* -> code (Sprache), blockquote -> quote,
aside.admonition -> callout, details/summary -> toggle, table/figure.table -> table, figure.image/img -> image,
hr -> delimiter, section/figure.include-note -> Absatz mit Seitenverweis, span.math-tex mit \\[..\\] allein im Absatz ->
math (an seiner Stelle). Inline: b/strong, i/em, u, s/del, code, mark, a, br, span.math-tex (\\(..\\)) -> code.hk-math. Leerraum wie im
Browser zusammengefasst (Trilium formatiert den Export eingerueckt); leere Absaetze (&nbsp;) fallen weg.

Verweise loest der Aufrufer auf (Ctx): link(href) -> ("page", "$ref:x") | ("file", "$file:k", name, is_image) |
("url", url) | None; image(src) -> ("file", "$file:k", name, is_image) | None; include(note_id) -> ("page", "$ref:x",
titel) | None. Grenzen: Verschachtelung MAX_TREE (tiefer: nur Text), Listen/Toggles MAX_DEPTH, Code CODE_MAX Zeichen."""
import re
from html import escape
from html.parser import HTMLParser

from saga.md2blocks import CALLOUT_EMOJI, _prot

MAX_TREE = 120
MAX_DEPTH = 6
CODE_MAX = 100_000
ROWS_MAX = 500
VOID = {"br", "img", "hr", "input", "meta", "link", "col", "wbr", "source", "base", "area", "embed", "param", "track"}
SKIP = {"script", "style", "head", "title", "template", "noscript", "iframe", "object", "svg", "math", "button", "select",
        "textarea", "canvas", "video", "audio"}
BLOCK = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "pre", "blockquote", "aside", "figure", "table", "hr",
         "details", "section", "div", "article", "main", "header", "footer", "nav", "dl", "dt", "dd", "li", "summary",
         "figcaption", "address", "body", "html", "thead", "tbody", "tfoot", "tr", "td", "th", "caption"}
INLINE_MAP = {"b": "b", "strong": "b", "i": "i", "em": "i", "u": "u", "s": "s", "del": "s", "strike": "s"}
_WS = re.compile(r"[ \t\n\r\f]+")


class Node:
    __slots__ = ("tag", "a", "kids")

    def __init__(self, tag, a=None):
        self.tag, self.a, self.kids = tag, a or {}, []

    def cls(self):
        return (self.a.get("class") or "").split()

    def text(self):
        out = []
        st = [self]
        while st:
            n = st.pop()
            if isinstance(n, str):
                out.append(n)
            elif n.tag == "br":
                out.append("\n")
            else:
                st.extend(reversed(n.kids))
        return "".join(out)


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.stack = [self.root]
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if self.skip:
            if tag in SKIP:
                self.skip += 1
            return
        if tag in SKIP:
            self.skip = 1
            return
        if len(self.stack) > MAX_TREE:              # zu tief: Elemente weg, Text bleibt
            return
        n = Node(tag, {k: (v or "") for k, v in attrs[:20]})
        self.stack[-1].kids.append(n)
        if tag not in VOID:
            self.stack.append(n)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID and not self.skip and self.stack[-1].tag == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        if self.skip:
            if tag in SKIP:
                self.skip -= 1
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if not self.skip and data:
            self.stack[-1].kids.append(data)


def parse_tree(html):
    p = _Tree()
    p.feed(html)
    p.close()
    return p.root


class Ctx:
    def __init__(self, link=None, image=None, include=None, report=None):
        self.link = link or (lambda href: None)
        self.image = image or (lambda src: None)
        self.include = include or (lambda nid: None)
        self.report = report or (lambda code, **p: None)
        self.n = 0
        self.used = set()                 # $file:k, die als Bild-/Datei-Block an ihrer Stelle stehen

    def bid(self):
        self.n += 1
        return f"th{self.n}"


def content_root(html):
    """Trilium-HTML-Export (ganze Seite mit Vorlage) -> Knoten mit dem eigentlichen Inhalt (div.ck-content), sonst body
    bzw. alles. Die Titel-Ueberschrift der Vorlage (h1[data-trilium-h1]) faellt weg."""
    root = parse_tree(html)
    found = _find(root, lambda n: n.tag == "div" and "ck-content" in n.cls())
    if found is None:
        found = _find(root, lambda n: n.tag == "body") or root
        found.kids = [k for k in found.kids if not (isinstance(k, Node) and "data-trilium-h1" in k.a)]
    return found


def _find(root, pred):
    st = [root]
    while st:
        n = st.pop()
        if isinstance(n, Node):
            if pred(n):
                return n
            st.extend(reversed(n.kids))
    return None


def convert(html, ctx, full_page=False):
    root = content_root(html) if full_page else parse_tree(html)
    return _blocks(root.kids, ctx, 0)


# =====================================================================================================
# Inline
# =====================================================================================================
class _Inline:
    """Inline-Inhalt -> Segmente [("html", s) | ("block", blk)] (Bilder als eigene Bloecke, wenn erlaubt)."""

    def __init__(self, ctx, images=True):
        self.ctx, self.images = ctx, images
        self.seg, self.buf, self.sp = [], [], True

    def text(self, s):
        s = _WS.sub(" ", s)
        if self.sp and s.startswith(" "):
            s = s[1:]
        if not s:
            return
        self.buf.append(_prot(escape(s, quote=False)))
        self.sp = s.endswith(" ")

    def raw(self, s):
        self.buf.append(s)

    def br(self):
        if self.buf and self.buf[-1].endswith(" "):
            self.buf[-1] = self.buf[-1].rstrip(" ")
        self.buf.append("<br>")
        self.sp = True

    def flush(self):
        h = _trim("".join(self.buf))
        if h:
            self.seg.append(("html", h))
        self.buf, self.sp = [], True

    def node(self, n, in_link=False):
        if isinstance(n, str):
            self.text(n)
            return
        tag, ctx = n.tag, self.ctx
        if tag == "br":
            self.br()
            return
        if tag == "img":
            blk = _image(n, ctx, None) if self.images and not in_link else None
            if blk is not None:
                self.flush()
                self.seg.append(("block", blk))
            else:
                alt = n.a.get("alt") or ""
                if alt:
                    self.text(alt)
            return
        if tag == "input":
            return
        if tag == "span" and "math-tex" in n.cls():
            tex = n.text().strip()
            m = re.fullmatch(r"\\([(\[])([\s\S]*)\\[)\]]", tex)
            display = bool(m and m.group(1) == "[")
            tex = (m.group(2) if m else tex).strip()
            if tex and display and self.images:           # \[..\]: eigener Mathe-Block an seiner Stelle
                self.flush()
                self.seg.append(("block", {"id": self.ctx.bid(), "type": "math", "data": {"tex": tex[:4000]}}))
            elif tex:
                self.raw('<code class="hk-math">' + _prot(escape(tex, quote=False)) + "</code>")
                self.sp = False
            return
        if tag == "code":
            t = _WS.sub(" ", n.text())
            if t.strip():
                self.raw('<code class="inline-code">' + _prot(escape(t, quote=False)) + "</code>")
                self.sp = False
            return
        if tag == "a":
            self._link(n, in_link)
            return
        if tag in ("ul", "ol", "table", "pre", "blockquote", "figure", "aside", "details"):
            self.text(" " + n.text() + " ")       # Block mitten im Inline-Kontext (z. B. Tabelle in Zitat): Text
            return
        wrap = INLINE_MAP.get(tag)
        if tag == "mark":
            wrap = "mark"
        if wrap:
            self.raw('<mark class="cdx-marker">' if wrap == "mark" else f"<{wrap}>")
        for k in n.kids:
            self.node(k, in_link)
        if wrap:
            self.raw(f"</{wrap}>")
        if tag in BLOCK and tag not in ("span",):  # p/div in Zellen u. a.: Zeilenumbruch
            self.br()

    def _link(self, n, in_link):
        href = (n.a.get("href") or "").strip()
        r = self.ctx.link(href) if href and not in_link else None
        start = len(self.buf)
        for k in n.kids:
            self.node(k, True)
        label = "".join(self.buf[start:])
        if not label.strip():
            label = ""
        del self.buf[start:]
        if r and r[0] == "page":
            if not label and len(r) > 2:
                label = _prot(escape(str(r[2]), quote=False))
            self.raw(f'<a href="/notes#{r[1]}">{label or "↪"}</a>')
        elif r and r[0] == "file":
            self.raw(f'<a href="{r[1]}">{label or escape(r[2], quote=False)}</a>')
        elif r and r[0] == "url":
            self.raw(f'<a href="{escape(r[1], quote=True)}">{label or escape(r[1], quote=False)}</a>')
        else:
            self.raw(label)
        self.sp = False

    def result(self):
        self.flush()
        return self.seg


def _trim(h):
    h = re.sub(r"^(?:\s|\xa0|<br>)+", "", h)
    h = re.sub(r"(?:\s|\xa0|<br>)+$", "", h)
    plain = re.sub(r"<[^>]*>", "", h).replace("\xa0", "").strip()
    if not plain and "hk-math" not in h:
        return ""
    return h


def inline_html(nodes, ctx):
    """Knoten -> ein Inline-HTML (Bilder als Text/alt), fuer Ueberschriften, Zellen, Listenpunkte."""
    il = _Inline(ctx, images=False)
    for k in nodes:
        il.node(k)
    return "<br>".join(s for kind, s in il.result() if kind == "html")


# =====================================================================================================
# Bloecke
# =====================================================================================================
def _blocks(nodes, ctx, depth):
    out = []
    il = _Inline(ctx)

    def flush():
        nonlocal il
        for kind, x in il.result():
            out.append(x if kind == "block" else _para(ctx, x))
        il = _Inline(ctx)

    for n in nodes:
        if isinstance(n, str) or (n.tag not in BLOCK and n.tag != "img"):
            il.node(n)
            continue
        if n.tag == "img":
            il.node(n)
            continue
        flush()
        out.extend(_block(n, ctx, depth))
    flush()
    return out


def _para(ctx, html):
    return {"id": ctx.bid(), "type": "paragraph", "data": {"text": html}}


def _block(n, ctx, depth):
    tag, cls = n.tag, n.cls()
    if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        txt = inline_html(n.kids, ctx)
        return [{"id": ctx.bid(), "type": "header", "data": {"text": txt, "level": int(tag[1])}}] if txt else []
    if tag == "p":
        return _blocks(n.kids, ctx, depth) if any(isinstance(k, Node) and (k.tag == "img" or "math-tex" in k.cls())
                                                  for k in _walk_inline(n)) \
            else [b for b in [_para(ctx, inline_html(n.kids, ctx))] if b["data"].get("text")]
    if tag in ("ul", "ol"):
        items = _items(n, ctx, 0)
        if not items:
            return []
        style = "checklist" if "todo-list" in cls else "ordered" if tag == "ol" else "unordered"
        return [{"id": ctx.bid(), "type": "list", "data": {"style": style, "meta": {}, "items": items}}]
    if tag == "pre":
        return [_code(n, ctx)]
    if tag == "blockquote":
        txt = _flat(n.kids, ctx, depth)
        return [{"id": ctx.bid(), "type": "quote", "data": {"text": txt, "caption": "", "alignment": "left"}}] if txt else []
    if tag == "aside" and "admonition" in cls:
        kind = next((c for c in cls if c != "admonition"), "note")
        txt = _flat(n.kids, ctx, depth)
        return [{"id": ctx.bid(), "type": "callout", "data": {"emoji": CALLOUT_EMOJI.get(kind, "💡"), "text": txt}}] if txt else []
    if tag == "details":
        summ = next((k for k in n.kids if isinstance(k, Node) and k.tag == "summary"), None)
        rest = [k for k in n.kids if k is not summ]
        inner = _blocks(rest, ctx, depth + 1) if depth < MAX_DEPTH else []
        return [{"id": ctx.bid(), "type": "toggle", "data": {"title": inline_html(summ.kids, ctx) if summ else "", "open": False,
                                                              "blocks": inner}}]
    if tag in ("section", "figure") and "include-note" in cls:
        r = ctx.include(n.a.get("data-note-id") or "")
        if r:
            return [_para(ctx, f'↪ <a href="/notes#{r[1]}">{_prot(escape(str(r[2] or "↪"), quote=False))}</a>')]
        ctx.report("link_missing", target=n.a.get("data-note-id") or "")
        return []
    if tag == "table" or (tag == "figure" and "table" in cls):
        t = n if tag == "table" else _find(n, lambda x: x.tag == "table")
        return [_table(t, ctx)] if t is not None else []
    if tag == "figure" and ("image" in cls or _find(n, lambda x: x.tag == "img") is not None):
        img = _find(n, lambda x: x.tag == "img")
        cap = next((k for k in n.kids if isinstance(k, Node) and k.tag == "figcaption"), None)
        blk = _image(img, ctx, inline_html(cap.kids, ctx) if cap else "") if img is not None else None
        return [blk] if blk else []
    if tag == "hr":
        return [{"id": ctx.bid(), "type": "delimiter", "data": {}}]
    if tag == "div" and "page-break" in cls:
        return []
    if tag in ("td", "th", "tr", "thead", "tbody", "tfoot", "caption", "summary", "figcaption", "li", "dt", "dd"):
        return _blocks(n.kids, ctx, depth)
    return _blocks(n.kids, ctx, depth)                # div, section, article, dl ...: durchsichtig


def _walk_inline(n):
    st = list(n.kids)
    while st:
        k = st.pop()
        if isinstance(k, Node):
            yield k
            if k.tag != "a":
                st.extend(k.kids)


def _items(lst, ctx, depth):
    items = []
    for li in lst.kids:
        if not isinstance(li, Node) or li.tag != "li":
            continue
        sub, own = [], []
        for k in li.kids:
            if isinstance(k, Node) and k.tag in ("ul", "ol"):
                if depth + 1 < MAX_DEPTH:
                    sub.extend(_items(k, ctx, depth + 1))
                else:
                    own.append(" " + k.text())
            else:
                own.append(k)
        chk = _find(li, lambda x: x.tag == "input" and (x.a.get("type") or "").lower() == "checkbox")
        meta = {}
        if "todo-list" in lst.cls() or chk is not None:
            meta = {"checked": chk is not None and "checked" in chk.a}
        txt = inline_html(own, ctx)
        if not txt and not sub:
            continue
        items.append({"content": txt, "meta": meta, "items": sub})
    return items


LANG = {"text/x-yaml": "yaml", "text/yaml": "yaml", "application/x-yaml": "yaml", "application/yaml": "yaml",
        "text/x-sh": "bash", "application/x-sh": "bash", "text/x-shellscript": "bash", "application/x-bat": "bat",
        "text/x-dockerfile": "dockerfile", "application/json": "json", "application/ld+json": "json", "text/json": "json",
        "text/x-python": "python", "text/javascript": "javascript", "application/javascript": "javascript",
        "application/x-javascript": "javascript", "application/javascript;env=frontend": "javascript",
        "application/javascript;env=backend": "javascript", "text/jsx": "javascript", "text/typescript": "typescript",
        "application/typescript": "typescript", "text/typescript-jsx": "typescript", "text/x-sql": "sql", "text/x-mysql": "sql",
        "text/x-mariadb": "sql", "text/x-mssql": "sql", "text/x-pgsql": "sql", "text/x-plsql": "sql", "text/x-sqlite": "sql",
        "text/x-sqlite;schema=trilium": "sql", "text/x-nginx-conf": "nginx", "text/html": "xml", "text/xml": "xml",
        "application/xml": "xml", "image/svg+xml": "xml", "text/x-toml": "ini", "text/x-properties": "ini", "text/x-ini": "ini",
        "application/x-powershell": "powershell", "text/x-go": "go", "text/x-rustsrc": "rust", "text/css": "css",
        "text/x-scss": "css", "text/x-less": "css", "text/x-haskell": "haskell", "text/x-mermaid": "mermaid",
        "text/vnd.mermaid": "mermaid", "text/x-csrc": "c", "text/x-c++src": "cpp", "text/x-csharp": "csharp",
        "text/x-java": "java", "text/x-kotlin": "kotlin", "text/x-php": "php", "application/x-httpd-php": "php",
        "text/x-ruby": "ruby", "text/x-perl": "perl", "text/x-lua": "lua", "text/x-swift": "swift", "text/x-diff": "diff",
        "text/x-markdown": "markdown", "text/x-gfm": "markdown", "text/markdown": "markdown"}
_BY_NORM = {re.sub(r"[\W_]+", "-", k.lower()): v for k, v in LANG.items()}
ALIAS = {"sh": "bash", "shell": "bash", "zsh": "bash", "yml": "yaml", "py": "python", "js": "javascript", "ts": "typescript",
         "html": "xml", "toml": "ini", "conf": "ini", "docker": "dockerfile", "ps1": "powershell", "golang": "go", "rs": "rust",
         "plaintext": "", "text": "", "plain": "", "text-plain": ""}


def lang_of_mime(mime):
    m = str(mime or "").lower().strip()
    if m in LANG:
        return LANG[m]
    return LANG.get(m.split(";", 1)[0].strip(), "")


def lang_of_class(name):
    """CKEditor-Klasse language-<x>: Trilium schreibt den normierten Mime-Typ (language-text-x-yaml), andere Quellen
    den Sprachnamen (language-yaml)."""
    x = str(name or "").lower()
    if x in _BY_NORM:
        return _BY_NORM[x]
    if x in ALIAS:
        return ALIAS[x]
    x = re.sub(r"[^a-z0-9+-]", "", x)[:20]
    return x if x and not x.startswith(("text-", "application-")) else ""


def _code(n, ctx):
    code = _find(n, lambda x: x.tag == "code") or n
    lang = ""
    for c in code.cls() + n.cls():
        if c.startswith("language-"):
            lang = lang_of_class(c[9:])
            break
    txt = code.text().replace("\r\n", "\n").strip("\n")
    if len(txt) > CODE_MAX:
        ctx.report("truncated")
        txt = txt[:CODE_MAX]
    return {"id": ctx.bid(), "type": "code", "data": {"code": txt, "language": lang}}


def code_block(text, mime, ctx):
    """Code-Notiz -> genau ein Code-Block (Sprache aus dem Mime-Typ)."""
    txt = str(text or "").replace("\r\n", "\n")
    if len(txt) > CODE_MAX:
        ctx.report("truncated")
        txt = txt[:CODE_MAX]
    return {"id": ctx.bid(), "type": "code", "data": {"code": txt, "language": lang_of_mime(mime)}}


def _table(t, ctx):
    rows, head = [], False
    for part in t.kids:
        if not isinstance(part, Node):
            continue
        trs = [part] if part.tag == "tr" else [x for x in part.kids if isinstance(x, Node) and x.tag == "tr"] \
            if part.tag in ("thead", "tbody", "tfoot") else []
        for tr in trs:
            cells = [c for c in tr.kids if isinstance(c, Node) and c.tag in ("td", "th")]
            if not rows:
                head = part.tag == "thead" or (cells and all(c.tag == "th" for c in cells))
            rows.append([_cell(c, ctx) for c in cells])
            if len(rows) >= ROWS_MAX:
                break
    if not rows:
        return {"id": ctx.bid(), "type": "paragraph", "data": {"text": ""}}
    w = max(len(r) for r in rows) or 1
    return {"id": ctx.bid(), "type": "table", "data": {"withHeadings": bool(head), "content": [r + [""] * (w - len(r)) for r in rows]}}


def _cell(c, ctx):
    return _flat(c.kids, ctx, MAX_DEPTH)


def _flat(nodes, ctx, depth):
    """Mehrere Bloecke als ein Inline-Text (Zitat/Callout/Zelle kennen keine Unterbloecke)."""
    parts = []
    for blk in _blocks(nodes, ctx, MAX_DEPTH if depth >= MAX_DEPTH else depth + 1):
        d = blk.get("data") or {}
        if blk["type"] == "list":
            parts.extend(_list_lines(d.get("items") or []))
        elif blk["type"] == "code":
            parts.append('<code class="inline-code">' + escape(d.get("code", ""), quote=False).replace("\n", "<br>") + "</code>")
        elif blk["type"] in ("image", "attaches"):
            f = d.get("file") or {}
            ctx.used.discard(f.get("url"))           # steht nicht als Block da -> Datei unten als Anhang
            parts.append(escape(str(f.get("name") or ""), quote=False))
        elif blk["type"] == "math":
            parts.append('<code class="hk-math">' + escape(d.get("tex", ""), quote=False) + "</code>")
        elif "text" in d and d["text"]:
            parts.append(d["text"])
    return "<br>".join(p for p in parts if p)


def _list_lines(items, ind=""):
    out = []
    for it in items:
        mark = ("☑ " if it["meta"].get("checked") else "☐ ") if "checked" in (it.get("meta") or {}) else "• "
        out.append(ind + mark + (it.get("content") or ""))
        out.extend(_list_lines(it.get("items") or [], ind + "\xa0\xa0"))
    return out


def _image(img, ctx, caption):
    src = (img.a.get("src") or "").strip()
    r = ctx.image(src) if src else None
    if r and r[0] == "file":
        _, ref, name, is_img = r
        ctx.used.add(ref)
        f = {"url": ref, "name": name, "is_image": is_img}
        if is_img:
            return {"id": ctx.bid(), "type": "image", "data": {"file": f, "caption": caption or "", "withBorder": False,
                                                               "stretched": False, "withBackground": False}}
        return {"id": ctx.bid(), "type": "attaches", "data": {"file": f, "title": escape(name, quote=False)}}
    if src.lower().startswith(("http://", "https://")):
        ctx.report("ext_image", target=src[:200])
        return {"id": ctx.bid(), "type": "paragraph",
                "data": {"text": f'<a href="{escape(src, quote=True)}">{escape(img.a.get("alt") or src, quote=False)}</a>'}}
    if src and not src.lower().startswith("data:"):
        ctx.report("link_missing", target=src[:200])
    alt = img.a.get("alt") or ""
    return {"id": ctx.bid(), "type": "paragraph", "data": {"text": escape(alt, quote=False)}} if alt else None
