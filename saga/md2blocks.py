"""Markdown -> Editor.js-Bloecke (2.5, Paket 2 E4). Eigener kleiner Parser (nur Standardbibliothek): CommonMark-Teilmenge
+ GFM + Obsidian/Notion-Eigenheiten. Die Ausgabe geht immer noch durch pages.clean_blocks (Positivliste fuer Inline-HTML).

Bloecke: Ueberschriften (# und Setext), Absaetze, Listen (verschachtelt, geordnet, Aufgaben), Zitate, Callouts
(> [!note] Titel, faltbar mit -/+ -> Toggle), Codebloecke (``` und ~~~), Trennlinie, GFM-Tabellen, Bilder als eigene
Zeile, <details><summary> -> Toggle, <aside> -> Callout, Frontmatter (wird vorher abgetrennt, siehe split_frontmatter).
Inline: **fett**, *kursiv*/_kursiv_, ~~durch~~, `code`, ==markiert==, $Mathe$, [Text](url), <url>, [[Wikilink|Alias]],
%%Kommentar%% (entfernt). HTML im Text wird als Text behandelt (Klartext, escaped).

Verweise loest der Aufrufer auf: resolve_link(ziel, ist_wikilink) -> ("page", "$ref:x") | ("db", "$ref:x") |
("file", "$file:k", name, is_image) | ("url", url) | None (Text bleibt, Bericht). Dateien/Bilder werden
"$file:k"-Bloecke (siehe import_common). Eine Zeile, die nur aus einem Link auf eine Datenbank besteht, wird ein
Datenbank-Block, nur ein Link auf eine Datei ein Datei-/Bild-Block (Notion-Export).

Notion (2.5, P2-E5): $$ ... $$ (eigene Zeilen) -> Mathe-Block, <columns><column> -> Spalten (bis 6, Rest in die letzte),
<callout icon=".." color=".."> -> Callout, Farben <span color="red">, <span class="highlight-red_background">,
<mark class="highlight-..."> -> Inline-Vertrag (span.hk-c-*, mark.hk-bg-*), {color="red"} am Zeilenende -> Blockfarbe
(tunes.hkstyle). Unbekannte Farben fallen weg (Text bleibt)."""
import re
from html import escape

CALLOUT_EMOJI = {"note": "ℹ️", "info": "ℹ️", "abstract": "📌", "summary": "📌", "tldr": "📌", "todo": "📌", "tip": "✅",
                 "hint": "✅", "important": "🔥", "success": "✅", "check": "✅", "done": "✅", "question": "💡", "help": "💡",
                 "faq": "💡", "warning": "⚠️", "caution": "⚠️", "attention": "⚠️", "failure": "❗", "fail": "❗",
                 "missing": "❗", "danger": "❗", "error": "❗", "bug": "❗", "example": "💡"}
EMOJI_OK = ["💡", "⚠️", "✅", "❗", "ℹ️", "🔥", "📌"]
# Notion-Emoji -> eines der 7 erlaubten (Rest: 💡)
EMOJI_MAP = {"⚠": "⚠️", "🚨": "❗", "❌": "❗", "⛔": "❗", "🛑": "❗", "✔️": "✅", "✔": "✅", "☑️": "✅", "👍": "✅",
             "📝": "📌", "📍": "📌", "📎": "📌", "🗒️": "📌", "ℹ": "ℹ️", "🔎": "ℹ️", "❓": "💡", "💭": "💡", "⭐": "🔥", "🚀": "🔥"}
PALETTE = ("gray", "brown", "orange", "yellow", "green", "blue", "purple", "pink", "red")
COLS_MAX = 6
MAX_DEPTH = 6

_FENCE = re.compile(r"^( {0,3})(`{3,}|~{3,})\s*([^`\s]*)[^`]*$")
_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_HR = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_LIST = re.compile(r"^( *)([-+*]|\d{1,9}[.)])(?:[ \t]+(.*)|$)")
_TASK = re.compile(r"^\[([ xX])\][ \t]+(.*)$")
_QUOTE = re.compile(r"^ {0,3}> ?(.*)$")
_CALLOUT = re.compile(r"^\[!([A-Za-z-]+)\]([-+]?)[ \t]*(.*)$")
_TABLE_SEP = re.compile(r"^ *\|? *:?-{1,}:? *(\| *:?-{1,}:? *)*\|? *$")
_IMG_LINE = re.compile(r"^\s*(!\[\[[^\]]+\]\]|!\[[^\]]*\]\([^)]*\))\s*$")
_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_BCOLOR = re.compile(r"\s*\{\s*color\s*=\s*\"?([a-z_]+)\"?\s*\}\s*$")
_LINK_LINE = re.compile(r"^\s*\[([^\]]*)\]\(<?([^)>\s]+)>?\)\s*$")


def color_of(raw):
    """Notion-Farbe ('red', 'red_background', 'highlight-red_bg', 'default') -> (palette, ist_hintergrund) | None."""
    x = str(raw or "").strip().lower().replace("highlight-", "").replace("-", "_")
    bg = x.endswith(("_background", "_bg"))
    x = x.rsplit("_b", 1)[0] if bg else x
    x = "gray" if x == "grey" else x
    return (x, bg) if x in PALETTE else None


class Ctx:
    """resolve(target, wiki) -> siehe Moduldoc; report(code, **p): Hinweis fuer den Bericht."""

    def __init__(self, resolve=None, report=None, link_blocks=False):
        self.resolve = resolve or (lambda target, wiki: None)
        self.report = report or (lambda code, **p: None)
        self.link_blocks = link_blocks              # Notion: Zeile nur mit Link auf Datenbank/Datei -> eigener Block
        self.n = 0

    def bid(self):
        self.n += 1
        return f"md{self.n}"


# =====================================================================================================
# Frontmatter
# =====================================================================================================
def split_frontmatter(text):
    """-> (frontmatter-dict, rest). Einfache YAML-Teilmenge: key: wert, key: [a, b], key:\\n  - a\\n  - b."""
    if not text.startswith("---"):
        return {}, text
    lines = text.split("\n")
    if lines[0].strip() != "---":
        return {}, text
    end = next((i for i in range(1, min(len(lines), 400)) if lines[i].strip() in ("---", "...")), None)
    if end is None:
        return {}, text
    fm, key = {}, None
    for ln in lines[1:end]:
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        m = re.match(r"^\s+-\s+(.*)$", ln)
        if m and key is not None:
            if not isinstance(fm.get(key), list):
                fm[key] = []
            fm[key].append(_yaml_scalar(m.group(1)))
            continue
        m = re.match(r"^([^:\s][^:]*):\s*(.*)$", ln)
        if not m:
            continue
        key, v = m.group(1).strip(), m.group(2).strip()
        if v.startswith("[") and v.endswith("]"):
            fm[key] = [_yaml_scalar(x) for x in _split_flow(v[1:-1]) if x.strip()]
        elif v == "":
            fm[key] = ""
        else:
            fm[key] = _yaml_scalar(v)
    return fm, "\n".join(lines[end + 1:])


def _split_flow(s):
    out, cur, q = [], "", None
    for ch in s:
        if q:
            cur += ch
            if ch == q:
                q = None
        elif ch in "\"'":
            q = ch
            cur += ch
        elif ch == ",":
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return out


def _yaml_scalar(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


# =====================================================================================================
# Inline
# =====================================================================================================
_INLINE = re.compile(
    r"(?P<code>`+)(?P<codetxt>.+?)(?P=code)"
    r"|(?P<wimg>!\[\[(?P<wimgt>[^\]|]+)(?:\|[^\]]*)?\]\])"
    r"|(?P<wiki>\[\[(?P<wikit>[^\]|#^]*)(?:[#^][^\]|]*)?(?:\|(?P<wikia>[^\]]*))?\]\])"
    r"|(?P<img>!\[(?P<imga>[^\]]*)\]\((?P<imgu>[^)\s]*)(?:\s+\"[^\"]*\")?\))"
    r"|(?P<link>\[(?P<linkt>(?:[^\[\]]|\[[^\]]*\])*)\]\((?P<linku><[^>]*>|(?:[^()\s]|\([^()\s]*\))*)(?:\s+\"[^\"]*\")?\))"
    r"|(?P<auto><(?P<autou>(?:https?://|mailto:)[^>\s]+)>)"
    r"|(?P<bare>(?<![\w/\"'=])(?P<bareu>https?://[^\s<>()\[\]]*[^\s<>()\[\].,;:!?\"']))"
    r"|(?P<esc>\\[\\`*_{}\[\]()#+\-.!|~=<>$])"
    # 2.5 (Notion): Farben <span color="red">..</span>, <span class="highlight-red">, <mark class="highlight-.._background">
    r"|(?P<span><(?P<stag>span|mark)\s(?P<sattr>[^<>]{0,200})>(?P<stxt>(?:(?!</?(?:span|mark)\b).){0,2000}?)</(?P=stag)>)"
    # 2.5: Inline-Mathe wie Obsidian - $TeX$ ohne Leerzeichen innen am Rand, nicht vor einer Ziffer ($5 und $10 bleibt Text)
    r"|(?P<math>(?<![\\$\w])\$(?P<mtxt>[^\s$][^$\n]{0,1998}?(?<=[^\s$\\]))\$(?![\w$]))"
)


def inline(s, ctx):
    """Markdown-Zeile(n) -> sicheres Inline-HTML (b, i, s, code, mark, a, br)."""
    s = re.sub(r"%%.*?%%", "", s, flags=re.S)
    out, pos = [], 0
    for m in _INLINE.finditer(s):
        out.append(_emph(s[pos:m.start()]))
        pos = m.end()
        if m.group("code"):
            out.append('<code class="inline-code">' + _prot(escape(m.group("codetxt").strip(), quote=False)) + "</code>")
        elif m.group("wimg"):
            out.append(_link_html(ctx, m.group("wimgt").strip(), True, None, embed=True))
        elif m.group("wiki"):
            tgt = (m.group("wikit") or "").strip()
            out.append(_link_html(ctx, tgt, True, m.group("wikia")))
        elif m.group("img"):
            u = m.group("imgu")
            out.append(_link_html(ctx, u, False, m.group("imga") or u.rsplit("/", 1)[-1], embed=True))
        elif m.group("link"):
            u = m.group("linku").strip("<>")
            out.append(_link_html(ctx, u, False, m.group("linkt"), inner=True))
        elif m.group("auto") or m.group("bare"):
            u = m.group("autou") or m.group("bareu")
            out.append(f'<a href="{escape(u, quote=True)}">{escape(u, quote=False)}</a>')
        elif m.group("math"):                    # Inline-Vertrag (P6-0): code.hk-math mit dem TeX-Text
            out.append('<code class="hk-math">' + _prot(escape(m.group("mtxt"), quote=False)) + "</code>")
        elif m.group("esc"):
            out.append(escape(m.group("esc")[1], quote=False))
        elif m.group("span"):
            out.append(_color_span(m.group("sattr"), m.group("stxt"), ctx))
    out.append(_emph(s[pos:]))
    return "".join(out).replace("\n", "<br>")


def _color_span(attr, txt, ctx):
    inner = inline(txt, ctx)
    m = re.search(r"""(?:color|class)\s*=\s*["']([^"']{1,80})["']""", attr or "")
    c = None
    for x in (m.group(1).split() if m else []):
        c = color_of(x)
        if c:
            break
    if not c:
        return inner
    return f'<mark class="hk-bg-{c[0]}">{inner}</mark>' if c[1] else f'<span class="hk-c-{c[0]}">{inner}</span>'


def _prot(s):
    return s.replace("$ref:", "&#36;ref:").replace("$file:", "&#36;file:") if "$" in s else s


def _emph(s):
    s = _prot(escape(s, quote=False))
    s = re.sub(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", r"<b>\2</b>", s)
    s = re.sub(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])", r"<i>\1</i>", s)
    s = re.sub(r"(?<![\w_])_(?=\S)(.+?)(?<=\S)_(?![\w_])", r"<i>\1</i>", s)
    s = re.sub(r"~~(?=\S)(.+?)(?<=\S)~~", r"<s>\1</s>", s)
    s = re.sub(r"==(?=\S)(.+?)(?<=\S)==", r'<mark class="cdx-marker">\1</mark>', s)
    return s


def _link_html(ctx, target, wiki, label, embed=False, inner=False):
    from urllib.parse import unquote
    text = label if label not in (None, "") else (target.rsplit("/", 1)[-1] if wiki else target)
    lbl = inline(text, ctx) if inner else _prot(escape(text, quote=False))
    r = ctx.resolve(unquote(target) if not wiki else target, wiki)
    if r and r[0] in ("page", "db"):
        if embed:
            ctx.report("embed_note", target=target)
            return f'↪ <a href="/notes#{r[1]}">{lbl}</a>'
        return f'<a href="/notes#{r[1]}">{lbl}</a>'
    if r and r[0] == "file":                     # Datei mitten im Text: Link auf die Datei
        return f'<a href="{r[1]}">{escape(label or r[2] or target, quote=False)}</a>'
    if r and r[0] == "url":
        return f'<a href="{escape(r[1], quote=True)}">{lbl}</a>'
    low = target.lower()
    if not wiki and low.startswith(("http://", "https://", "mailto:")):
        if embed:
            ctx.report("ext_image", target=target)
        return f'<a href="{escape(target, quote=True)}">{lbl}</a>'
    if not wiki and re.match(r"^[a-z][a-z0-9+.-]*:", low):     # javascript:, data: & Co. -> nur Text
        return lbl
    ctx.report("link_missing", target=target)
    return lbl


# =====================================================================================================
# Bloecke
# =====================================================================================================
def parse(text, ctx=None, depth=0):
    """Markdown-Text -> Liste von Bloecken."""
    ctx = ctx or Ctx()
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    text = re.sub(r"%%.*?%%", "", text, flags=re.S)
    lines = text.split("\n")
    out, i, para = [], 0, []

    def flush():
        if para:
            txt = "\n".join(x.strip() for x in para).strip()
            col = None
            mc = _BCOLOR.search(txt)
            if mc:
                col, txt = color_of(mc.group(1)), txt[:mc.start()]
            lm = _LINK_LINE.match(txt) if ctx.link_blocks and "\n" not in txt else None
            blk = _link_block(lm, ctx) if lm else None
            if blk is None and txt:
                blk = _p(ctx, inline(txt, ctx))
            if blk is not None:
                out.append(_tint(blk, col))
            para.clear()

    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            flush()
            i += 1
            continue
        m = _FENCE.match(ln)
        if m:
            flush()
            fence, lang, ind = m.group(2), m.group(3), len(m.group(1))
            code, i = [], i + 1
            while i < len(lines) and not re.match(r"^ {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*$", lines[i]):
                code.append(lines[i][ind:] if lines[i][:ind].strip() == "" else lines[i])
                i += 1
            i += 1
            out.append({"id": ctx.bid(), "type": "code", "data": {"code": "\n".join(code)[:100000],
                                                                  "language": re.sub(r"[^a-z0-9+-]", "", lang.lower())[:20]}})
            continue
        if ln.strip().startswith("$$"):
            flush()
            blk, i = _math(lines, i, ctx)
            out.append(blk)
            continue
        m = _ATX.match(ln)
        if m:
            flush()
            txt, col = (m.group(2) or "").strip(), None
            mc = _BCOLOR.search(txt)
            if mc:
                col, txt = color_of(mc.group(1)), txt[:mc.start()].strip()
            out.append(_tint({"id": ctx.bid(), "type": "header", "data": {"text": inline(txt, ctx), "level": min(len(m.group(1)), 6)}}, col))
            i += 1
            continue
        if para and _SETEXT.match(ln) and not _HR.match(ln) or (para and re.match(r"^ {0,3}=+[ \t]*$", ln)):
            txt = "\n".join(x.strip() for x in para)
            para.clear()
            out.append({"id": ctx.bid(), "type": "header", "data": {"text": inline(txt, ctx), "level": 1 if ln.strip()[0] == "=" else 2}})
            i += 1
            continue
        if _HR.match(ln):
            flush()
            out.append({"id": ctx.bid(), "type": "delimiter", "data": {}})
            i += 1
            continue
        if _QUOTE.match(ln):
            flush()
            q = []
            while i < len(lines) and lines[i].strip() and _QUOTE.match(lines[i]):
                q.append(_QUOTE.match(lines[i]).group(1))
                i += 1
            out.append(_quote(q, ctx, depth))
            continue
        lm = _LIST.match(ln)
        if lm and (not para or lm.group(2) in "-+*" or lm.group(2).startswith("1")):
            flush()
            block, i = _list(lines, i, ctx)
            out.append(block)
            continue
        if "|" in ln and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1]) and lines[i + 1].count("-") >= 1:
            flush()
            rows = [_cells(ln)]
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_cells(lines[i]))
                i += 1
            w = max(len(r) for r in rows)
            out.append({"id": ctx.bid(), "type": "table", "data": {"withHeadings": True, "content": [
                [inline(c, ctx) for c in r] + [""] * (w - len(r)) for r in rows[:500]]}})
            continue
        low = ln.strip().lower()
        if low.startswith("<details"):
            flush()
            blk, i = _details(lines, i, ctx, depth)
            out.append(blk)
            continue
        if low.startswith("<aside"):
            flush()
            blk, i = _aside(lines, i, ctx, depth)
            out.append(blk)
            continue
        if low.startswith("<callout"):
            flush()
            blk, i = _callout(lines, i, ctx, depth)
            out.append(blk)
            continue
        if low.startswith("<columns"):
            flush()
            got, i = _columns(lines, i, ctx, depth)
            out.extend(got)
            continue
        if _IMG_LINE.match(ln):
            flush()
            out.append(_media(ln.strip(), ctx))
            i += 1
            continue
        para.append(ln)
        i += 1
    flush()
    return out


def _p(ctx, html):
    return {"id": ctx.bid(), "type": "paragraph", "data": {"text": html}}


def _cells(ln):
    s = ln.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", s)]


def _quote(q, ctx, depth):
    m = _CALLOUT.match(q[0].strip()) if q else None
    if m:
        kind, fold, title = m.group(1).lower(), m.group(2), m.group(3).strip()
        rest = q[1:]
        if kind == "quote" or kind == "cite":
            return {"id": ctx.bid(), "type": "quote", "data": {"text": inline("\n".join(rest).strip() or title, ctx),
                                                               "caption": inline(title, ctx) if rest else "", "alignment": "left"}}
        if fold:
            inner = parse("\n".join(rest), ctx, depth + 1) if depth < MAX_DEPTH else []
            return {"id": ctx.bid(), "type": "toggle", "data": {"title": inline(title or kind.capitalize(), ctx), "open": fold == "+",
                                                                "blocks": inner}}
        emoji = CALLOUT_EMOJI.get(kind, "💡")
        body = "\n".join(x for x in rest).strip()
        txt = ("<b>" + inline(title, ctx) + "</b>" + ("<br>" if body else "") if title else "") + _flat(body, ctx)
        return {"id": ctx.bid(), "type": "callout", "data": {"emoji": emoji, "text": txt}}
    inner = "\n".join(q)
    return {"id": ctx.bid(), "type": "quote", "data": {"text": _flat(inner, ctx), "caption": "", "alignment": "left"}}


def _flat(md, ctx):
    """Mehrere Markdown-Absaetze als ein Inline-Text (Zitat/Callout kennen keine Unterbloecke)."""
    parts = []
    for blk in parse(md, ctx, MAX_DEPTH):
        d = blk.get("data") or {}
        if blk["type"] == "list":
            parts.extend("• " + it.get("content", "") for it in d.get("items") or [])
        elif "text" in d:
            parts.append(d["text"])
        elif blk["type"] == "code":
            parts.append('<code class="inline-code">' + escape(d.get("code", ""), quote=False) + "</code>")
    return "<br>".join(parts)


def _list(lines, i, ctx):
    """Liste ab Zeile i (verschachtelt ueber Einrueckung) -> (Block, naechste Zeile)."""
    first = _LIST.match(lines[i])
    ordered = first.group(2)[0].isdigit()
    task = bool(first.group(3) and _TASK.match(first.group(3)))
    style = "checklist" if task else "ordered" if ordered else "unordered"
    root = {"items": []}
    stack = [(len(first.group(1)) - 1, root)]
    last = None
    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if _LIST.match(nxt) or (nxt.startswith("  ") and last is not None):
                i += 1
                continue
            break
        m = _LIST.match(ln)
        if m:
            ind = len(m.group(1))
            content = m.group(3) or ""
            meta = {}
            tm = _TASK.match(content)
            if tm:
                content = tm.group(2)
                meta = {"checked": tm.group(1).lower() == "x"}
            elif style == "checklist":
                meta = {"checked": False}
            while len(stack) > 1 and ind <= stack[-1][0]:
                stack.pop()
            if len(stack) == 1 and root["items"] and _kind(m.group(2), bool(tm)) != style:
                break                                    # andere Listenart auf oberster Ebene: neue Liste
            if ind > stack[-1][0] + 1 and last is not None and len(stack) < MAX_DEPTH:
                stack.append((ind - 1, last))
            item = {"content": inline(content.strip(), ctx), "meta": meta, "items": [], "_raw": content.strip()}
            stack[-1][1]["items"].append(item)
            last = item
            i += 1
            continue
        if last is not None and (ln.startswith("  ") or not _block_start(ln)):
            if ln.startswith("  ") or not ln.strip().startswith(("#", ">", "```", "|")):
                last["_raw"] += "\n" + ln.strip()
                last["content"] = inline(last["_raw"], ctx)
                i += 1
                continue
        break

    def strip(items):
        for it in items:
            it.pop("_raw", None)
            strip(it["items"])
    strip(root["items"])
    return {"id": ctx.bid(), "type": "list", "data": {"style": style, "meta": {}, "items": root["items"]}}, i


def _kind(marker, task):
    return "checklist" if task else "ordered" if marker[0].isdigit() else "unordered"


def _block_start(ln):
    return bool(_ATX.match(ln) or _HR.match(ln) or _QUOTE.match(ln) or _FENCE.match(ln)
                or ln.strip().lower().startswith(("<details", "<aside", "<callout", "<columns", "$$")))


def _html_block(lines, i, tag):
    """Zeilen von <tag ...> bis </tag> (verschachtelt gezaehlt) -> (innerer Text, naechste Zeile)."""
    buf, level = [], 0
    while i < len(lines):
        ln = lines[i]
        level += len(re.findall(rf"<{tag}\b", ln, re.I)) - len(re.findall(rf"</{tag}\s*>", ln, re.I))
        buf.append(ln)
        i += 1
        if level <= 0:
            break
    txt = "\n".join(buf)
    txt = re.sub(rf"^\s*<{tag}\b[^>]*>", "", txt, flags=re.I)
    txt = re.sub(rf"</{tag}\s*>\s*$", "", txt, flags=re.I)
    return txt, i


def _details(lines, i, ctx, depth):
    txt, i = _html_block(lines, i, "details")
    m = re.search(r"<summary[^>]*>(.*?)</summary>", txt, re.I | re.S)
    title = re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""
    if m:
        txt = txt[:m.start()] + txt[m.end():]
    inner = parse(txt, ctx, depth + 1) if depth < MAX_DEPTH else []
    return {"id": ctx.bid(), "type": "toggle", "data": {"title": inline(title, ctx), "open": False, "blocks": inner}}, i


def _aside(lines, i, ctx, depth):
    """Notion-Callout: <aside> Emoji Text </aside>."""
    txt, i = _html_block(lines, i, "aside")
    txt = txt.strip()
    emoji = "💡"
    for e in sorted(EMOJI_OK, key=len, reverse=True):
        if txt.startswith(e):
            emoji, txt = e, txt[len(e):].strip()
            break
    else:
        m = re.match(r"^([^\w\s<>\[\]#*_`~]{1,4})\s+", txt)
        if m:
            emoji, txt = _emoji(m.group(1)), txt[m.end():]
    return {"id": ctx.bid(), "type": "callout", "data": {"emoji": emoji, "text": _flat(txt, ctx)}}, i


def _emoji(e):
    e = (e or "").strip()
    if e in EMOJI_OK:
        return e
    return EMOJI_MAP.get(e) or EMOJI_MAP.get(e.replace("\ufe0f", "")) or "💡"


def _callout(lines, i, ctx, depth):
    """Notion-Markdown (neuere Form): <callout icon="💡" color="gray_bg"> Text </callout>."""
    first = lines[i]
    txt, i = _html_block(lines, i, "callout")
    m = re.search(r"""icon\s*=\s*["']([^"']{1,16})["']""", first)
    mc = re.search(r"""color\s*=\s*["']([^"']{1,40})["']""", first)
    blk = {"id": ctx.bid(), "type": "callout", "data": {"emoji": _emoji(m.group(1)) if m else "💡", "text": _flat(txt.strip(), ctx)}}
    return _tint(blk, color_of(mc.group(1)) if mc else None), i


def _columns(lines, i, ctx, depth):
    """<columns><column>..</column>..</columns> -> Spalten-Block (2..6; mehr: Rest in die letzte Spalte, eine: flach)."""
    txt, i = _html_block(lines, i, "columns")
    cols = [m.group(1) for m in re.finditer(r"<column\b[^>]*>(.*?)</column\s*>", txt, re.I | re.S)]
    if not cols:
        return (parse(txt, ctx, depth + 1) if depth < MAX_DEPTH else []), i
    parsed = [parse(c, ctx, depth + 1) if depth < MAX_DEPTH else [] for c in cols]
    if len(parsed) == 1:
        return parsed[0], i
    if len(parsed) > COLS_MAX:
        ctx.report("columns_max")
        parsed = parsed[:COLS_MAX - 1] + [[b for c in parsed[COLS_MAX - 1:] for b in c]]
    return [{"id": ctx.bid(), "type": "columns", "data": {"cols": [{"blocks": c} for c in parsed]}}], i


def _math(lines, i, ctx):
    """$$ TeX $$ in einer Zeile oder ueber mehrere Zeilen -> Mathe-Block."""
    first = lines[i].strip()[2:]
    if first.rstrip().endswith("$$") and first.strip() != "":
        return {"id": ctx.bid(), "type": "math", "data": {"tex": first.rstrip()[:-2].strip()[:4000]}}, i + 1
    buf, i = ([first] if first.strip() else []), i + 1
    while i < len(lines):
        ln = lines[i]
        if ln.strip().endswith("$$"):
            rest = ln.strip()[:-2]
            if rest.strip():
                buf.append(rest)
            i += 1
            break
        buf.append(ln)
        i += 1
    return {"id": ctx.bid(), "type": "math", "data": {"tex": "\n".join(buf).strip()[:4000]}}, i


def _tint(blk, col):
    """Blockfarbe (Notion {color=..}) -> tunes.hkstyle."""
    if col and blk.get("type") in ("paragraph", "header", "list", "quote", "toggle", "callout"):
        st = {"c": col[0]}
        if col[1]:
            st["bg"] = True
        blk["tunes"] = {"hkstyle": st}
    return blk


def _link_block(m, ctx):
    """Absatz, der nur aus einem Link besteht: Datenbank -> Datenbank-Block, Datei -> Bild/Anhang; sonst None."""
    from urllib.parse import unquote
    label, target = m.group(1), m.group(2)
    if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) and not target.lower().startswith(("http://", "https://")):
        return None
    r = ctx.resolve(unquote(target), False)
    if r and r[0] == "db":
        return {"id": ctx.bid(), "type": "database", "data": {"db": r[1]}}
    if r and r[0] == "file":
        _, ref, name, is_img = r
        f = {"url": ref, "name": name, "is_image": is_img}
        if is_img:
            return {"id": ctx.bid(), "type": "image", "data": {"file": f, "caption": escape(label if label != name else "", quote=False),
                                                               "withBorder": False, "stretched": False, "withBackground": False}}
        return {"id": ctx.bid(), "type": "attaches", "data": {"file": f, "title": escape(label or name, quote=False)}}
    return None


def _media(ln, ctx):
    """Bild-/Dateizeile -> image/attaches-Block (Datei aus dem Import) oder Absatz mit Link."""
    m = re.match(r"^!\[\[([^\]|]+)(?:\|[^\]]*)?\]\]$", ln)
    if m:
        target, alt, wiki = m.group(1).strip(), "", True
    else:
        m = re.match(r"^!\[([^\]]*)\]\(([^)\s]*)(?:\s+\"[^\"]*\")?\)$", ln)
        target, alt, wiki = m.group(2), m.group(1), False
    from urllib.parse import unquote
    r = ctx.resolve(unquote(target) if not wiki else target, wiki)
    if r and r[0] == "file":
        _, ref, name, is_img = r
        f = {"url": ref, "name": name, "is_image": is_img}
        if is_img:
            return {"id": ctx.bid(), "type": "image", "data": {"file": f, "caption": escape(alt, quote=False),
                                                               "withBorder": False, "stretched": False, "withBackground": False}}
        return {"id": ctx.bid(), "type": "attaches", "data": {"file": f, "title": escape(name, quote=False)}}
    if r and r[0] in ("page", "db"):
        ctx.report("embed_note", target=target)
        return _p(ctx, f'↪ <a href="/notes#{r[1]}">{escape(alt or target.rsplit("/", 1)[-1], quote=False)}</a>')
    if target.lower().startswith(("http://", "https://")):
        ctx.report("ext_image", target=target)
        return _p(ctx, f'<a href="{escape(target, quote=True)}">{escape(alt or target, quote=False)}</a>')
    ctx.report("link_missing", target=target)
    return _p(ctx, escape(alt or target, quote=False))
