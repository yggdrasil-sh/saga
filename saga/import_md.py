"""Markdown-/Obsidian-Ordner importieren (2.5, Paket 2 E4). Quelle: ZIP oder mehrere Dateien (Ordner-Upload).

Baum: eine Sammelseite (Name des Ordners bzw. "Markdown-Import <Datum>"), Ordner = Seite (Inhalt aus gleichnamiger
Notiz Ordner/Ordner.md bzw. index.md/README.md), .md-Dateien = Seiten, .obsidian/, .trash/ und versteckte Dateien werden
ignoriert. Wikilinks nach Obsidian-Regel: exakter relativer Pfad, dann eindeutiger Dateiname im Tresor (Gross/Klein
egal), sonst Bericht. Bilder/Dateien im Upload -> image/attaches (Typ am Inhalt erkannt), externe Bilder bleiben Links.
Frontmatter (Entscheidung P2-E3): title -> Seitentitel, tags (+ Option) -> Saga-Tags, aliases/cssclass(es) ignoriert,
uebrige Schluessel (Option) -> ausklappbarer Block "Eigenschaften" oben in der Seite. Markdown je Datei max. 2 MB."""
import posixpath
import re
import time
from html import escape

from saga import i18n, md2blocks
from saga import import_common as ic
from saga.core import TZ, t
from saga.files import _sniff_image
from saga.notes import FILE_MAX

MD_MAX = 2 * ic.MB
MD_EXT = (".md", ".markdown")
IGNORE_DIRS = {".obsidian", ".trash", ".git", "__macosx", ".stfolder", "node_modules"}
DROP_KEYS = {"aliases", "alias", "cssclass", "cssclasses", "publish", "permalink"}


def _ignored(path):
    parts = path.split("/")
    return any(p.startswith(".") or p.lower() in IGNORE_DIRS for p in parts) or parts[-1].lower() in ("thumbs.db", "desktop.ini")


def _rel(src, path):
    """Pfad ohne gemeinsamen Wurzelordner."""
    r = src.root
    return path[len(r) + 1:] if r and path.startswith(r + "/") else path


class Tree:
    """Seitenbaum aus den Pfaden: folders {ordnerpfad: ref}, notes {md-pfad: ref}, files {pfad: key}."""

    def __init__(self, src, report):
        self.src, self.rep = src, report
        self.notes, self.folders, self.files, self.folder_note = {}, {}, {}, {}
        self.by_name, self.by_stem, self.file_by_name = {}, {}, {}
        n = 0
        for p in sorted(src.paths(), key=lambda x: x.lower()):
            rp = _rel(src, p)
            if _ignored(rp):
                continue
            low = rp.lower()
            if low.endswith(MD_EXT):
                n += 1
                self.notes[p] = f"n{n}"
                stem = posixpath.splitext(posixpath.basename(rp))[0].lower()
                self.by_stem.setdefault(stem, []).append(p)
                self.by_name.setdefault(posixpath.splitext(rp)[0].lower(), p)
            elif low.endswith((".canvas", ".excalidraw")) or src.entries[p].size == 0:
                report.skip(rp, "unsupported" if src.entries[p].size else "empty")
            else:
                n += 1
                self.files[p] = f"f{n}"
                self.file_by_name.setdefault(posixpath.basename(rp).lower(), []).append(p)
        k = 0
        for p in self.notes:
            d = posixpath.dirname(_rel(src, p))
            while d:
                if d not in self.folders:
                    k += 1
                    self.folders[d] = f"d{k}"
                d = posixpath.dirname(d)
        for d in self.folders:                       # Ordnernotiz: Ordner/Ordner.md, index.md, README.md
            base = posixpath.basename(d).lower()
            for p in self.notes:
                rp = _rel(src, p)
                if posixpath.dirname(rp) == d and posixpath.splitext(posixpath.basename(rp))[0].lower() in (base, "index", "readme"):
                    if d not in self.folder_note or posixpath.splitext(posixpath.basename(rp))[0].lower() == base:
                        self.folder_note[d] = p
        self.note_of_folder = {v: k for k, v in self.folder_note.items()}

    def ref_of_note(self, p):
        d = self.note_of_folder.get(p)
        return self.folders[d] if d else self.notes[p]

    def resolve(self, here, target, wiki):
        """Link aus der Notiz here -> ("page", "$ref:x") | ("file", "$file:k", name, is_image) | None."""
        target = target.split("#", 1)[0].split("^", 1)[0].strip()
        if not target or re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):
            return ("url", target) if target.lower().startswith(("http://", "https://", "mailto:")) else None
        hdir = posixpath.dirname(_rel(self.src, here))
        cand = posixpath.normpath(posixpath.join(hdir, target)) if not target.startswith("/") else target.lstrip("/")
        root = (self.src.root + "/") if self.src.root else ""
        low = cand.lower()
        # Notizen
        for name in (low, posixpath.splitext(low)[0]):
            p = self.by_name.get(name if not name.endswith(MD_EXT) else posixpath.splitext(name)[0])
            if p:
                return ("page", "$ref:" + self.ref_of_note(p))
        if wiki or not posixpath.splitext(target)[1] or target.lower().endswith(MD_EXT):
            stem = posixpath.splitext(posixpath.basename(target))[0].lower() if target.lower().endswith(MD_EXT) else posixpath.basename(target).lower()
            hits = self.by_stem.get(stem) or []
            if "/" in target:
                hits = [h for h in hits if _rel(self.src, h).lower().endswith(posixpath.splitext(target.lower())[0] + posixpath.splitext(h)[1].lower())] or hits
            if hits:
                return ("page", "$ref:" + self.ref_of_note(sorted(hits, key=lambda h: (h.count("/"), h))[0]))
        # Dateien
        p = root + cand if (root + cand) in self.files else cand if cand in self.files else None
        if p is None:
            hits = self.file_by_name.get(posixpath.basename(target).lower()) or []
            p = sorted(hits, key=lambda h: (h.count("/"), h))[0] if hits else None
        if p is not None:
            return ("file", "$file:" + self.files[p], posixpath.basename(p), self.is_image(p))
        return None

    def is_image(self, p):
        if not hasattr(self, "_img"):
            self._img = {}
        if p not in self._img:
            try:
                with self.src.open(p) as f:
                    head = f.read(16)
            except Exception:  # noqa: BLE001 - kaputte Datei: Anhang
                head = b""
            self._img[p] = bool(_sniff_image(head))
        return self._img[p]


def analyze(src, report):
    tr = Tree(src, report)
    big = [p for p in tr.files if src.entries[p].size > FILE_MAX]
    warn = []
    if big:
        warn.append(t("{n} Dateien sind größer als {mb} MB und werden nicht übernommen.", n=len(big), mb=FILE_MAX // ic.MB))
    unresolved = 0
    for p in list(tr.notes)[:300]:                   # Stichprobe fuer die Anzeige (alles erst beim Lauf)
        try:
            txt = src.read(p, MD_MAX).decode("utf-8", "replace")
        except ic.ImportFail:
            continue
        for m in re.finditer(r"\[\[([^\]|#^]+)", txt):
            if tr.resolve(p, m.group(1), True) is None:
                unresolved += 1
    return {"md": {"pages": len(tr.notes) - len(tr.note_of_folder) + len(tr.folders), "folders": len(tr.folders), "files": len(tr.files),
                   "unresolved": unresolved, "title": src.root or ""}, "warnings": warn}


def _title_from(path, fm, blocks):
    v = fm.get("title")
    if isinstance(v, str) and v.strip():
        return v.strip()
    return posixpath.splitext(posixpath.basename(path))[0]


def _tags_from(fm):
    v = fm.get("tags", fm.get("tag"))
    if isinstance(v, str):
        v = [x for x in re.split(r"[,\s]+", v) if x]
    if not isinstance(v, list):
        return []
    out = []
    for x in v:
        x = str(x).strip().lstrip("#").replace("/", "-")[:30]
        if x:
            out.append(x)
    return out[:20]


def _props_block(fm, ctx):
    rows = []
    for k, v in fm.items():
        if k.lower() in DROP_KEYS or k.lower() in ("title", "tags", "tag"):
            continue
        val = ", ".join(str(x) for x in v) if isinstance(v, list) else str(v)
        rows.append({"content": "<b>" + escape(str(k), quote=False) + ":</b> " + escape(val, quote=False)[:500], "meta": {}, "items": []})
    if not rows:
        return None
    return {"id": ctx.bid(), "type": "toggle", "data": {"title": escape(t("Eigenschaften"), quote=False), "open": False, "blocks": [
        {"id": ctx.bid(), "type": "list", "data": {"style": "unordered", "meta": {}, "items": rows[:100]}}]}}


def work(tok, b, parent):
    """Lauf fuer importer.start: work(run, src) -> (root, parent)."""
    want_tags, want_props = b.get("tags", True) is not False, b.get("props", True) is not False
    title = str(b.get("title") or "").strip()[:200]

    def go(run, src):
        rep = run.report
        run.step("analyze", 0, 0, force=True)
        tr = Tree(src, rep)
        if not tr.notes:
            raise ic.ImportFail("nothing")
        root_title = title or src.root or t("Markdown-Import {date}", date=i18n.fmt_date(time.time(), TZ))
        items = [{"ref": "root", "kind": "page", "parent": None, "title": root_title, "icon": "📥"}]
        for d in sorted(tr.folders, key=lambda x: (x.count("/"), x.lower())):
            par = tr.folders.get(posixpath.dirname(d)) or "root"
            it = {"ref": tr.folders[d], "kind": "page", "parent": par, "title": posixpath.basename(d), "icon": "📁"}
            if d in tr.folder_note:
                _fill(it, tr, tr.folder_note[d], src, rep, want_tags, want_props, folder=True)
            items.append(it)
        for p, ref in tr.notes.items():
            if p in tr.note_of_folder:
                continue
            d = posixpath.dirname(_rel(src, p))
            it = {"ref": ref, "kind": "page", "parent": tr.folders.get(d) or "root"}
            _fill(it, tr, p, src, rep, want_tags, want_props)
            items.append(it)
        files = {}
        for p, key in tr.files.items():
            files[key] = {"name": posixpath.basename(p), "size": src.entries[p].size, "path": _rel(src, p),
                          "open": (lambda p=p: src.open(p))}
        plan = {"items": items, "files": files}
        # nur Dateien, die eine Notiz wirklich benutzt (Ordner-Uploads enthalten oft mehr), Rest im Bericht
        used = _used_files(tr, src)
        for p, key in tr.files.items():
            if key not in used:
                rep.note(files[key]["path"], "ignored")
                del files[key]
            elif files[key]["size"] > FILE_MAX:
                rep.skip(files[key]["path"], "too_big", mb=FILE_MAX // ic.MB)
                del files[key]
        return ic.apply_plan(run, plan, parent), parent
    return go


_LINKS = re.compile(r"!?\[\[([^\]|#^]+)|!?\[[^\]]*\]\(<?([^)>\s]+)")


def _used_files(tr, src):
    """Schlanker Durchlauf: welche Dateien verlinken die Notizen? (ohne Bloecke im Speicher zu halten)"""
    from urllib.parse import unquote
    used = set()
    for p in tr.notes:
        try:
            txt = src.read(p, MD_MAX).decode("utf-8", "replace")
        except ic.ImportFail:
            continue
        for m in _LINKS.finditer(txt):
            wiki = m.group(1) is not None
            r = tr.resolve(p, m.group(1) if wiki else unquote(m.group(2)), wiki)
            if r and r[0] == "file":
                used.add(r[1][len("$file:"):])
    return used


def _fill(it, tr, path, src, rep, want_tags, want_props, folder=False):
    rp = _rel(src, path)
    try:
        raw = src.read(path, MD_MAX)
    except ic.ImportFail as ex:
        rep.skip(rp, ex.code, **ex.p)
        it.setdefault("title", posixpath.splitext(posixpath.basename(rp))[0])
        return
    txt = raw.decode("utf-8", "replace").lstrip("﻿")
    fm, body = md2blocks.split_frontmatter(txt)
    if not folder:
        it["title"] = _title_from(rp, fm, None)
    elif isinstance(fm.get("title"), str) and fm["title"].strip():
        it["title"] = fm["title"].strip()
    if want_tags:
        it["tags"] = _tags_from(fm)
    for k in fm:
        if k.lower() in DROP_KEYS:
            rep.note(rp, "ignored", key=k)

    def blocks():
        ctx = md2blocks.Ctx(lambda target, wiki: tr.resolve(path, target, wiki),
                            lambda code, **p: rep.note(f"{rp}: {p.get('target', '')}" if p.get("target") else rp, code))
        out = md2blocks.parse(body, ctx)
        # erste Ueberschrift = Titel (Notion/Obsidian): doppelt -> weg
        if out and out[0]["type"] == "header" and re.sub(r"<[^>]+>", "", out[0]["data"].get("text", "")).strip() == it.get("title"):
            out = out[1:]
        pb = _props_block(fm, ctx) if want_props else None
        return ([pb] if pb else []) + out
    it["body"] = blocks
    it["path"] = rp
