import json,sys
o=json.loads(sys.stdin.read().strip().split('\n')[-1])
print(o['tag'], o['who'], 'cpu', o['cpu_pct'], 'load', o.get('load'))
for p in o['peers']:
  print(' ', p['peer'], p['path'], p['via'], 'first', p['first'], 'srcFps', p.get('srcFps'))
  for v in p['video']:
    if v['fps']: print('    V', {k: v[k] for k in ['slot','fps','kbps','hMed','frz','frzS','drop','keys','jb','lat50','lat95','latN','hue']})
  for a in p['audio']:
    if a['kbps']: print('    A', {k: a[k] for k in ['slot','kbps','conc','concEv','jb','beep50','beep95','beepN','peakHz']})
  if p.get('e2e') or p.get('e2eC') or p.get('fe'): print('    E2E', json.dumps({k: p.get(k) for k in ['e2e','e2eC','fe']})[:900])
  if p.get('ws'): print('    WS', {k: p['ws'].get(k) for k in ['audio','video']}, 'up', {k: p['ws']['up'].get(k) for k in ['target','rate']})
