# Baut static/offline.html aus der Vorlage: CSP-Hashes fuer das Inline-<style> und -<script>
# Aufruf: python3 tools/mkoffline.py tools/offline.tmpl.html static/offline.html
import base64, hashlib, re, sys
src, dst = sys.argv[1], sys.argv[2]
s = open(src, encoding="utf-8").read()
def h(x): return "sha256-" + base64.b64encode(hashlib.sha256(x.encode("utf-8")).digest()).decode()
st = re.search(r"<style>(.*?)</style>", s, re.S).group(1)
sc = re.search(r"<script>(.*?)</script>", s, re.S).group(1)
s = s.replace("__STYLE__", h(st)).replace("__SCRIPT__", h(sc))
open(dst, "w", encoding="utf-8").write(s)
print(h(st), h(sc))
