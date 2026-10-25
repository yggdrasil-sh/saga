"""Fake fuer das selfh.st-CDN (ICON_CDN=http://127.0.0.1:18310 im Test-Server): /png/<name>.png fuer einige Namen,
sonst 404. Genutzt von tests/dienste.py und tests/favicon.py - so laufen die Symbol-Tests ohne Internet."""
import io
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

COLORS = {"nextcloud": (0, 130, 201), "youtube": (255, 0, 0), "github": (24, 23, 23), "jellyfin": (170, 92, 195)}
HITS = []


def png(rgb, size=64):
    b = io.BytesIO()
    Image.new("RGBA", (size, size), rgb + (255,)).save(b, "PNG")
    return b.getvalue()


ICONS = {n: png(c) for n, c in COLORS.items()}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        HITS.append(self.path)
        name = self.path[5:-4] if self.path.startswith("/png/") and self.path.endswith(".png") else None
        data = ICONS.get(name)
        if data is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def start(url):
    """url = ICON_CDN des Servers; startet nur, wenn er auf 127.0.0.1 zeigt. -> Server oder None."""
    from urllib.parse import urlparse
    pu = urlparse(url or "")
    if pu.hostname != "127.0.0.1" or not pu.port:
        return None
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", pu.port), H)
    except OSError:                      # laeuft schon (anderer Test im selben Lauf)
        return None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
