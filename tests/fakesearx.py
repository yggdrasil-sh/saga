"""Attrappe fuer SearXNG (nur Tests): POST /search -> feste JSON-Treffer zum Suchbegriff.
   python3 tests/fakesearx.py <port>   (Server dann mit SEARXNG_URL=http://127.0.0.1:<port> SEARXNG_SECRET=test starten)"""
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        f = parse_qs(self.rfile.read(n).decode())
        q, page = (f.get("q") or [""])[0], int((f.get("pageno") or ["1"])[0])
        res = [{"title": f"{q} – Treffer {(page - 1) * 3 + i + 1}", "url": f"https://example.org/{page}/{i}",
                "content": f"Ausschnitt zu {q}", "engines": ["attrappe"]} for i in range(3)]
        res.append({"title": "boese", "url": "javascript:alert(1)", "content": ""})
        body = json.dumps({"results": res, "answers": [f"Antwort zu {q}"] if page == 1 else [],
                           "suggestions": [], "corrections": [], "unresponsive_engines": []}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
