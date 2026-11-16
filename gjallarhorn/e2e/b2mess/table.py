# Tabelle aus $B2M_DIR/runs/<tag>.json: python3 table.py TAG...
import json, os, sys
WD = os.environ.get("B2M_DIR", "/tmp/b2/stand")
def f(x): return '-' if x is None else (str(round(x, 1)) if isinstance(x, float) else str(x))
print('| Lauf | Weg DUT | Ab: Bild B/s / Hoehe / kbit/s | Ab: Bild-Latenz p50/p95 (n) | Ab: Einfrieren (n/s) | Ab: Ton-Latenz p50/p95 (n) / Verdeckung % | Auf: Bild B/s / Hoehe / Latenz p50/p95 | Auf: Ton p50/p95 | Erstes Bild ms | CPU Chromium % |')
print('|---|---|---|---|---|---|---|---|---|---|')
for t in sys.argv[1:]:
    try: o = json.load(open(f'{WD}/runs/{t}.json'))['out']
    except Exception as e: print('|', t, '| fehlt |'); continue
    d, u = o['peers'][0], o['peers'][1]
    v = next((x for x in d['video'] if x['fps']), {}); a = next((x for x in d['audio'] if x['kbps']), {})
    uv = next((x for x in u['video'] if x['fps']), {}); ua = next((x for x in u['audio'] if x['kbps']), {})
    print(f"| {t} | {d['path']} | {f(v.get('fps'))} / {f(v.get('hMed'))} / {f(v.get('kbps'))} | {f(v.get('lat50'))}/{f(v.get('lat95'))} ({f(v.get('latN'))}) | {f(v.get('frz'))}/{f(v.get('frzS'))} | {f(a.get('beep50'))}/{f(a.get('beep95'))} ({f(a.get('beepN'))}) / {f(a.get('conc'))} | {f(uv.get('fps'))} / {f(uv.get('hMed'))} / {f(uv.get('lat50'))}/{f(uv.get('lat95'))} | {f(ua.get('beep50'))}/{f(ua.get('beep95'))} | {f(min(d['first'].values()) if d['first'] else None)} | {f(o['cpu_pct']['chrome'])} |")
