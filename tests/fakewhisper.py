"""Fake-Diktat-Dienst fuer tests/kollegin_diktat.py (Saga 2.6, Bugs-DB 2274 K-F): Whisper-kompatibel
(POST /v1/audio/transcriptions, multipart: file, model, language, response_format, temperature). Lauscht nur auf
127.0.0.1 (Port aus argv[1], Standard 18296) und auf Port+1 (Ziel der Weiterleitung, zaehlt dort). Speichert nichts.

Verhalten nach Modellname:
  fake           200 {"text": " <lang>: Erinner mich Freitag an den Pavillon KANARIE-DIKTAT-K16\x07 "} (Rand und Steuerzeichen
                 entfernt der Server; die Reihe sucht die Kanarie im Log)
  fake-slow      wartet 8 s (Zeitlimit des Servers 5 s -> 504)
  fake-hang      wartet 2,5 s, dann wie fake (Gleichzeitigkeit: busy/full)
  fake-redirect  302 auf http://127.0.0.1:<Port+1>/v1/audio/transcriptions (Weiterleitungen sind verboten)
  fake-500       HTTP 500
  fake-big       200 mit 70 KB JSON (Antwort zu gross)
  fake-notjson   200 "hallo" (kein JSON)
  fake-notext    200 {"segments": []} (ohne text)
  fake-auth      verlangt 'Authorization: Bearer sk-stt-123' (sonst 401)
  fake-wavonly   nur WAV (RIFF), sonst 400 wie whisper.cpp ohne ffmpeg
  fake-blank     200 {"text": " [BLANK_AUDIO]"}
  fake-long      200 mit 1 500 Zeichen Text (Server kuerzt auf 1 000, cut)
Zaehler GET /_count {n, redirect}; GET /_last {model, language, response_format, temperature, filename, ctype, size, head,
auth}; POST /_reset."""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 18296
CANARY = "KANARIE-DIKTAT-K16"
STATE = {"n": 0, "redirect": 0, "last": {}}
LOCK = threading.Lock()


def multipart(body, ctype):
    """multipart/form-data -> {name: (filename|None, ctype|None, bytes)} (klein, nur fuer Tests)."""
    b = None
    for part in ctype.split(";"):
        part = part.strip()
        if part.startswith("boundary="):
            b = part[9:].strip('"')
    if not b:
        return {}
    out = {}
    for chunk in body.split(b"--" + b.encode())[1:]:
        if chunk.startswith(b"--"):
            break
        chunk = chunk[2:] if chunk.startswith(b"\r\n") else chunk
        head, _, data = chunk.partition(b"\r\n\r\n")
        data = data[:-2] if data.endswith(b"\r\n") else data
        name = fn = ct = None
        for line in head.decode("latin-1").split("\r\n"):
            low = line.lower()
            if low.startswith("content-disposition:"):
                for kv in line.split(";")[1:]:
                    k, _, v = kv.strip().partition("=")
                    if k == "name":
                        name = v.strip('"')
                    elif k == "filename":
                        fn = v.strip('"')
            elif low.startswith("content-type:"):
                ct = line.split(":", 1)[1].strip()
        if name:
            out[name] = (fn, ct, data)
    return out


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj, raw=None):
        data = raw if raw is not None else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/_count":
            with LOCK:
                return self._json(200, {"n": STATE["n"], "redirect": STATE["redirect"]})
        if self.path == "/_last":
            with LOCK:
                return self._json(200, STATE["last"])
        self._json(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        if self.path == "/_reset":
            with LOCK:
                STATE.update(n=0, redirect=0, last={})
            return self._json(200, {"ok": True})
        if self.server.server_address[1] == PORT + 1:              # Ziel der Weiterleitung: darf nie erreicht werden
            with LOCK:
                STATE["redirect"] += 1
            return self._json(200, {"text": "weitergeleitet"})
        if self.path != "/v1/audio/transcriptions":
            return self._json(404, {"error": "not found"})
        f = multipart(body, self.headers.get("Content-Type") or "")
        val = lambda k: (f.get(k) or (None, None, b""))[2].decode("utf-8", "replace")
        model, lang = val("model"), val("language")
        fn, ct, audio = f.get("file") or (None, None, b"")
        with LOCK:
            STATE["n"] += 1
            STATE["last"] = {"model": model, "language": lang, "response_format": val("response_format"),
                             "temperature": val("temperature"), "filename": fn, "ctype": ct, "size": len(audio),
                             "head": audio[:4].hex(), "auth": self.headers.get("Authorization")}
        if model == "fake-slow":
            time.sleep(8)
        if model == "fake-hang":
            time.sleep(2.5)
        if model == "fake-redirect":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{PORT + 1}/v1/audio/transcriptions")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if model == "fake-500":
            return self._json(500, {"error": "kaputt"})
        if model == "fake-big":
            return self._json(200, {"text": "x" * (70 * 1024)})
        if model == "fake-notjson":
            return self._json(200, None, raw=b"hallo")
        if model == "fake-notext":
            return self._json(200, {"segments": []})
        if model == "fake-auth" and self.headers.get("Authorization") != "Bearer sk-stt-123":
            return self._json(401, {"error": "unauthorized"})
        if model == "fake-wavonly" and not audio.startswith(b"RIFF"):
            return self._json(400, {"error": "failed to read audio"})
        if model == "fake-blank":
            return self._json(200, {"text": " [BLANK_AUDIO]"})
        if model == "fake-long":
            return self._json(200, {"text": ("Wort " * 300).strip()})
        self._json(200, {"text": f" {lang}: Erinner mich Freitag an den Pavillon {CANARY}\x07 "})


def main():
    servers = [ThreadingHTTPServer(("127.0.0.1", p), H) for p in (PORT, PORT + 1)]
    for s in servers[1:]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    servers[0].serve_forever()


if __name__ == "__main__":
    main()
