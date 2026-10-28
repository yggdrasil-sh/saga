"""Diagramm als statisches SVG (2.5, Paket 4) - fuer veroeffentlichte Seiten (saga.publish), ohne JS und ohne style-Attribute
(CSP). Eingabe: Ergebnis von saga.dbcalc.chart. Farben ueber Klassen (static/public.css: .ch-c-<palette>, .ch-s<n>), damit
helles/dunkles Schema greifen. Gleiche Formen wie im Web (static/pages.js chartSvg): Balken, Balken quer, Linie, Flaeche,
Kreis, Ring, Zahl. Werte stehen zusaetzlich als Tabelle in <details> (Screenreader, Kopieren)."""
import math
from html import escape

from saga import i18n

W, H = 640, 300
PAD_L, PAD_R, PAD_T, PAD_B = 56, 16, 16, 56
SERIES_N = 8


def nice_ticks(lo, hi, n=5):
    """Schoene Achsenwerte (1, 2, 2.5, 5 x 10^k) von <= lo bis >= hi -> Liste (auch fuer die Apps: tests/chart_ticks.json)."""
    if lo == hi:
        hi = lo + 1 if lo >= 0 else lo
        lo = min(0, lo)
        if lo == hi:
            hi = lo + 1
    span = hi - lo
    raw = span / max(1, n)
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw - 1e-12)
    a = math.floor(lo / step + 1e-9) * step
    b = math.ceil(hi / step - 1e-9) * step
    out, x = [], a
    while x <= b + step / 2 and len(out) < 50:
        v = round(x, 10)
        out.append(int(v) if float(v).is_integer() else v)
        x += step
    return out


def _f(v, fmt):
    return i18n.fmt_num(v, fmt)


def _cls(color, i):
    return f"ch-c-{color}" if color else f"ch-s{i % SERIES_N}"


def _table(res):
    fmt = res.get("fmt")
    series = res.get("series") or []
    head = "".join(f"<th>{escape(s.get('label') or '')}</th>" for s in series) if len(series) > 1 else "<th></th>"
    rows = []
    for i, c in enumerate(res.get("cats") or []):
        cells = "".join(f"<td>{escape(_f((s.get('values') or [None] * (i + 1))[i] if i < len(s.get('values') or []) else None, fmt))}</td>" for s in series)
        rows.append(f"<tr><th>{escape(c.get('label') or '')}</th>{cells}</tr>")
    return (f'<details class="pub-ch-tab"><summary>{escape(i18n.t("Werte als Tabelle"))}</summary>'
            f'<table class="pub-table"><thead><tr><th></th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></details>')


def _cat_label(res, c):
    lab = c.get("label") or ""
    return lab if len(lab) <= 14 else lab[:13] + "…"


def render(res, title=""):
    """Ergebnis von dbcalc.chart -> HTML (SVG + Tabelle). Leer/unbekannt: ''."""
    if not isinstance(res, dict):
        return ""
    typ, fmt = res.get("type"), res.get("fmt")
    if typ == "number":
        return f'<div class="pub-wg-v">{escape(_f(res.get("total"), fmt))}</div>'
    cats = res.get("cats") or []
    series = res.get("series") or []
    if not cats or not series:
        return f'<p class="pub-muted">{escape(i18n.t("Keine Einträge."))}</p>'
    aria = escape(title or i18n.t("Diagramm##Chart"))
    out = [f'<svg class="pub-ch" viewBox="0 0 {W} {H}" role="img" aria-label="{aria}" xmlns="http://www.w3.org/2000/svg">']
    if typ in ("pie", "donut"):
        vals = [max(0.0, float(v)) if isinstance(v, (int, float)) else 0.0 for v in series[0].get("values") or []]
        tot = sum(vals)
        cx, cy, r = 150, H / 2, 120
        ri = 66 if typ == "donut" else 0
        a0 = -math.pi / 2
        for i, (c, v) in enumerate(zip(cats, vals)):
            if tot <= 0 or v <= 0:
                continue
            a1 = a0 + v / tot * 2 * math.pi
            if v >= tot:
                a1 = a0 + 2 * math.pi - 1e-4
            big = 1 if a1 - a0 > math.pi else 0
            p = [f"M{cx + r * math.cos(a0):.1f},{cy + r * math.sin(a0):.1f}",
                 f"A{r},{r} 0 {big} 1 {cx + r * math.cos(a1):.1f},{cy + r * math.sin(a1):.1f}"]
            if ri:
                p += [f"L{cx + ri * math.cos(a1):.1f},{cy + ri * math.sin(a1):.1f}",
                      f"A{ri},{ri} 0 {big} 0 {cx + ri * math.cos(a0):.1f},{cy + ri * math.sin(a0):.1f}Z"]
            else:
                p.append(f"L{cx},{cy}Z")
            out.append(f'<path class="ch-f {_cls(c.get("color"), i)}" d="{" ".join(p)}"><title>{escape(c.get("label") or "")}: {escape(_f(v, fmt))}</title></path>')
            a0 = a1
        if ri:
            out.append(f'<text class="ch-big" x="{cx}" y="{cy + 8}" text-anchor="middle">{escape(_f(res.get("total"), fmt))}</text>')
        for i, c in enumerate(cats[:12]):                      # Legende rechts
            y = 30 + i * 21
            out.append(f'<rect class="ch-f {_cls(c.get("color"), i)}" x="300" y="{y - 10}" width="12" height="12" rx="2"/>'
                       f'<text class="ch-t" x="318" y="{y}">{escape((c.get("label") or "")[:34])} · {escape(_f(vals[i] if i < len(vals) else None, fmt))}</text>')
        out.append("</svg>")
        return "".join(out) + _table(res)
    stack = res.get("stack") is True
    allv = []
    if stack:
        for i in range(len(cats)):
            pos = sum(max(0, s["values"][i]) for s in series if isinstance(s["values"][i], (int, float)))
            neg = sum(min(0, s["values"][i]) for s in series if isinstance(s["values"][i], (int, float)))
            allv += [pos, neg]
    else:
        allv = [v for s in series for v in s.get("values") or [] if isinstance(v, (int, float))]
    if isinstance(res.get("goal"), (int, float)):
        allv.append(res["goal"])
    lo, hi = min(allv + [0]), max(allv + [0])
    ticks = nice_ticks(lo, hi)
    lo, hi = ticks[0], ticks[-1]
    horiz = typ == "hbar"
    pw, ph = W - PAD_L - PAD_R, H - PAD_T - PAD_B
    if horiz:
        def vpos(v):
            return PAD_L + (v - lo) / (hi - lo) * pw
    else:
        def vpos(v):
            return PAD_T + ph - (v - lo) / (hi - lo) * ph
    for tk in ticks:                                           # Gitter + Achsenbeschriftung
        p = vpos(tk)
        if horiz:
            out.append(f'<line class="ch-g" x1="{p:.1f}" y1="{PAD_T}" x2="{p:.1f}" y2="{PAD_T + ph}"/>'
                       f'<text class="ch-t" x="{p:.1f}" y="{H - PAD_B + 16}" text-anchor="middle">{escape(_f(tk, fmt))}</text>')
        else:
            out.append(f'<line class="ch-g" x1="{PAD_L}" y1="{p:.1f}" x2="{PAD_L + pw}" y2="{p:.1f}"/>'
                       f'<text class="ch-t" x="{PAD_L - 6}" y="{p + 4:.1f}" text-anchor="end">{escape(_f(tk, fmt))}</text>')
    n = len(cats)
    band = (ph if horiz else pw) / n
    every = max(1, math.ceil(n / (8 if horiz else 12)))
    for i, c in enumerate(cats):                               # Kategorien
        if i % every:
            continue
        m = (PAD_T if horiz else PAD_L) + band * (i + .5)
        lab = escape(_cat_label(res, c))
        if horiz:
            out.append(f'<text class="ch-t" x="{PAD_L - 6}" y="{m + 4:.1f}" text-anchor="end">{lab}</text>')
        else:
            out.append(f'<text class="ch-t" x="{m:.1f}" y="{H - PAD_B + 16}" text-anchor="middle">{lab}</text>')
    if typ in ("bar", "hbar"):
        k = 1 if stack else len(series)
        bw = band * .78 / k
        base = vpos(0)
        acc_p, acc_n = [0.0] * n, [0.0] * n
        for si, s in enumerate(series):
            for i, v in enumerate(s.get("values") or []):
                if not isinstance(v, (int, float)):
                    continue
                if stack:
                    a = acc_p[i] if v >= 0 else acc_n[i]
                    b = a + v
                    if v >= 0:
                        acc_p[i] = b
                    else:
                        acc_n[i] = b
                    p0, p1 = vpos(a), vpos(b)
                    off = band * .11
                else:
                    p0, p1 = base, vpos(v)
                    off = band * .11 + si * bw
                start = (PAD_T if horiz else PAD_L) + band * i + off
                col = _cls(s.get("color") or (cats[i].get("color") if len(series) == 1 else None), si if len(series) > 1 else i if cats[i].get("color") else 0)
                tip = f"<title>{escape(cats[i].get('label') or '')}{' · ' + escape(s.get('label')) if s.get('label') else ''}: {escape(_f(v, fmt))}</title>"
                if horiz:
                    out.append(f'<rect class="ch-f {col}" x="{min(p0, p1):.1f}" y="{start:.1f}" width="{abs(p1 - p0):.1f}" height="{bw:.1f}" rx="2">{tip}</rect>')
                else:
                    out.append(f'<rect class="ch-f {col}" x="{start:.1f}" y="{min(p0, p1):.1f}" width="{bw:.1f}" height="{abs(p1 - p0):.1f}" rx="2">{tip}</rect>')
    else:                                                      # Linie / Flaeche
        for si, s in enumerate(series):
            pts = [(PAD_L + band * (i + .5), vpos(v)) for i, v in enumerate(s.get("values") or []) if isinstance(v, (int, float))]
            if not pts:
                continue
            col = _cls(s.get("color"), si)
            d = " ".join(f"{'M' if j == 0 else 'L'}{x:.1f},{y:.1f}" for j, (x, y) in enumerate(pts))
            if typ == "area":
                out.append(f'<path class="ch-a {col}" d="{d} L{pts[-1][0]:.1f},{vpos(max(lo, 0)):.1f} L{pts[0][0]:.1f},{vpos(max(lo, 0)):.1f}Z"/>')
            out.append(f'<path class="ch-l {col}" d="{d}"/>')
            for x, y in pts if len(pts) <= 40 else []:
                out.append(f'<circle class="ch-f {col}" cx="{x:.1f}" cy="{y:.1f}" r="3"/>')
    if isinstance(res.get("goal"), (int, float)):
        g = vpos(res["goal"])
        out.append(f'<line class="ch-goal" x1="{g:.1f}" y1="{PAD_T}" x2="{g:.1f}" y2="{PAD_T + ph}"/>' if horiz else
                   f'<line class="ch-goal" x1="{PAD_L}" y1="{g:.1f}" x2="{PAD_L + pw}" y2="{g:.1f}"/>')
    if len(series) > 1:                                        # Legende unten
        x = PAD_L
        for si, s in enumerate(series):
            lab = (s.get("label") or "")[:20]
            out.append(f'<rect class="ch-f {_cls(s.get("color"), si)}" x="{x}" y="{H - 18}" width="10" height="10" rx="2"/>'
                       f'<text class="ch-t" x="{x + 14}" y="{H - 9}">{escape(lab)}</text>')
            x += 24 + len(lab) * 7
            if x > W - 60:
                break
    out.append("</svg>")
    return "".join(out) + _table(res)
