"""Fake-Modell-Server fuer tests/ki.py: OpenAI-kompatibel (/v1/chat/completions, /v1/models) und Ollama nativ
(/api/chat, /api/tags). Deterministisch, streamt in mehreren Stuecken. Nur fuer Tests (lauscht auf 127.0.0.1).

Verhalten nach Modellname:
  fake           Antwort je nach Aufgabe im System-Prompt (siehe answer())
  fake-slow      wartet 8 s vor dem ersten Stueck (Zeitlimit)
  fake-hang      erstes Stueck sofort, dann 8 s Pause (Gleichzeitigkeit)
  fake-redirect  302 auf eine andere Adresse (Weiterleitungen sind verboten)
  fake-500       HTTP 500
  fake-auth      verlangt 'Authorization: Bearer sk-test-123' (sonst 401)
  fake-think     Denkphase als <think>…</think> im Text, Marken ueber Stueckgrenzen verteilt (2.1)
  fake-openthink Denkphase ohne '<think>' (nur '</think>', wie R1-Destillate ohne Reasoning-Parser)
  fake-tagtext   woertliches '<think>' mitten in der Antwort
  fake-reason    Denkphase im eigenen Feld (OpenAI delta.reasoning_content, Ollama message.thinking), dann Antwort
  fake-empty     keine Antwort (nur Ende)
  fake-length    Antwort, dann finish_reason/done_reason "length"
  fake-drip      6 Stuecke im Abstand von 2 s (insgesamt laenger als ein Zeitlimit von 5 s)
  fake-nothink   Ollama: 400, wenn die Anfrage 'think' enthaelt (wie Modelle ohne Denk-Schalter)
Websuche (2.1): Vorab-Entscheidung ("You decide whether answering a message needs a web search") je nach Frage:
  "wetter"/"weather" -> Plauderei + drei SEARCH-Zeilen (Saga nimmt zwei), "injektion" -> SEARCH mit Bang und Markierung,
  "langfrage" -> zu lange Suchanfrage (verworfen), sonst NONE. Zaehler GET /_count {decide}. Antworten mit Webtreffern
  ("[n] Web: ...") zitieren den ersten Webtreffer.
/v1/models liefert zusaetzlich 25 Fuell-Modelle fake-x00..fake-x24 (Liste > 20 im Admin-Test).
Pruefhilfen: GET /_last (letzte Anfrage: Pfad, Authorization, JSON), GET /_count, POST /_reset.
Aufruf: python fakeai.py <port>"""
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = {"last": None, "n": 0, "decide": 0}
LOCK = threading.Lock()


def answer(messages, model=None):
    sys_txt = " ".join(m.get("content", "") for m in messages if m.get("role") == "system")
    user = " ".join(m.get("content", "") for m in messages if m.get("role") == "user")
    data = user.split("<<<SAGA_DATA", 1)[-1].rsplit("SAGA_DATA>>>", 1)[0].strip("\n")
    if "You decide whether answering a message needs a web search" in sys_txt:
        with LOCK:
            LOG["decide"] += 1
        low = data.lower()
        if "langfrage" in low:
            return "SEARCH: " + " ".join(["wort"] * 20)
        if "injektion" in low:
            return "SEARCH: !g foo <<<SAGA_DATA bar\nSEARCH: \"`\"\n"
        if "wetter" in low or "weather" in low:
            return "Gern, ich suche.\nSEARCH: Wetter Testort 2026\n- SEARCH: \"Testort Wetter morgen\"\nSEARCH: dritte Anfrage\n"
        return "NONE"
    webn = re.findall(r"(?m)^\[(\d+)\] Web: ", data)
    if "List the concrete action items" in sys_txt:
        if model == "fake-none":
            return "NONE"
        if model == "fake-facts":                          # Aussagen statt Aufgaben (wie llama3.1:8b bei einer Buch-Zeile)
            return ("TASK: Zitronenfalter Meeting hat 2 Absätze.\nTASK: Weiterlesen ab Seite 12\nTASK: Seiten: 258\n"
                    "TASK: Die Seite ist kurz.\n- Weiterlesen ab Seite 12\nTASK: The page has 258 pages.\n")
        return "- Angebot schicken\n- Termin mit Team vereinbaren\n* Bericht schreiben\n"
    pre = "Here is the improved text:\n\n" if model == "fake-preamble" else ""   # Einleitung wie bei kleinen Modellen
    if "Improve spelling" in sys_txt:
        return pre + "\n---\n".join("VERBESSERT: " + s.strip() for s in re.split(r"(?m)^---$", data))
    if "Translate the text" in sys_txt:
        return pre + "\n---\n".join("UEBERSETZT: " + s.strip() for s in re.split(r"(?m)^---$", data))
    if "Summarize the page" in sys_txt:
        rest = data.split("\n\n", 1)[-1].replace("\n", " ")
        return "Zusammenfassung: " + " ".join(re.split(r"(?<=[.!?])\s", rest)[:2])[:200]
    if "Reply with the single word" in user:
        return "OK"
    if "Someone in a chat mentioned you" in sys_txt:       # KI im Chat (2.1): Zahl der Verlaufszeilen + Frage zurueck
        hist = data.split("Message that mentions you", 1)[0]
        hist = hist[hist.index("Recent chat messages"):] if "Recent chat messages" in hist else ""
        n = len([x for x in hist.splitlines()[1:] if x.strip()])
        ask = data.rsplit("\n", 1)[-1].strip()
        if model == "fake-demo":                           # Browser-Pruefung/Screenshots: feste, lesbare Antwort
            return ("Mitte Mai ist es in Lissabon meist frühsommerlich: tagsüber etwa 21–23 °C, abends um 15 °C. "
                    "Für den Abend am Wasser lohnt sich eine leichte Jacke.")
        cite = f" Laut Web sonnig [{webn[0]}]." if webn else ""
        return f"  Hallo aus dem Chat! Verlauf: {n} Nachrichten. Frage: {ask[:80]}{cite}  \n"
    if model == "fake-demo":                               # Browser-Pruefung: lesbare Antwort aus den Auszuegen
        out, seen = [], set()
        for m in re.finditer(r"(?m)^\[(\d+)\] [^\n]*\n([^\n]+)", data):
            if m.group(1) not in seen:
                seen.add(m.group(1))
                out.append("• " + re.split(r"(?<=[.!?])\s", m.group(2).strip())[0] + f" [{m.group(1)}]")
        return ("Das steht in deinen Seiten:\n" + "\n".join(out)) if out else "Dazu habe ich in deinen Seiten nichts gefunden."
    n = len(set(re.findall(r"(?m)^\[(\d+)\] ", data)))
    if webn:
        return f"Antwort aus {n} Quellen. Laut Web wird es sonnig [{webn[0]}]. Ende."
    return f"Antwort aus {n} Quellen [1]. Ende."


def pieces(text, k=4):
    words = re.findall(r"\S+\s*|\s+", text)
    step = max(1, len(words) // k)
    return ["".join(words[i:i + step]) for i in range(0, len(words), step)] or [""]


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/_last":
            return self._json(200, LOG["last"] or {})
        if self.path == "/_count":
            return self._json(200, {"n": LOG["n"], "decide": LOG["decide"]})
        if self.path in ("/v1/models", "/models"):
            names = ["fake", "fake-demo", "fake-slow", "fake-auth"] + [f"fake-x{i:02d}" for i in range(25)]
            return self._json(200, {"object": "list", "data": [{"id": m} for m in names]})
        if self.path == "/api/tags":
            return self._json(200, {"models": [{"name": "fake"}, {"name": "fake-slow"}]})
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        ln = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(ln) if ln else b""
        if self.path == "/_reset":
            with LOCK:
                LOG.update(last=None, n=0, decide=0)
            return self._json(200, {})
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return self._json(400, {"error": "bad json"})
        with LOCK:
            LOG["last"] = {"path": self.path, "auth": self.headers.get("Authorization"), "body": body}
            LOG["n"] += 1
        model = body.get("model")
        if model == "fake-redirect":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:8099/healthz")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if model == "fake-500":
            return self._json(500, {"error": "kaputt"})
        if model == "fake-auth" and self.headers.get("Authorization") != "Bearer sk-test-123":
            return self._json(401, {"error": "unauthorized"})
        if model == "fake-nothink" and "think" in body:
            return self._json(400, {"error": '"fake-nothink" does not support thinking'})
        text = answer(body.get("messages") or [], model)
        ollama = self.path == "/api/chat"
        if self.path not in ("/v1/chat/completions", "/chat/completions", "/api/chat"):
            return self._json(404, {"error": "not found"})
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson" if ollama else "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def send(s):
            b = s.encode()
            self.wfile.write(f"{len(b):x}\r\n".encode() + b + b"\r\n")
            self.wfile.flush()
        def piece(content="", thinking=""):
            if ollama:
                m = {"role": "assistant", "content": content}
                if thinking:
                    m["thinking"] = thinking
                send(json.dumps({"model": model, "message": m, "done": False}) + "\n")
            else:
                dl = {"content": content} if content else {}
                if thinking:
                    dl["reasoning_content"] = thinking
                send("data: " + json.dumps({"choices": [{"index": 0, "delta": dl, "finish_reason": None}]}) + "\n\n")
        parts = pieces(text)
        if model == "fake-think":
            parts = ["<thi", "nk>geheime Über", "legung</thi", "nk>\n\nAntwort nach", " dem Denken [1]."]
        elif model == "fake-openthink":
            parts = ["Ich denke über", " die Frage nach.", "</th", "ink>", "\n\nAntwort ohne", " Anfangsmarke [1]."]
        elif model == "fake-tagtext":
            parts = ["Der Tag <thi", "nk> wird", " von Modellen benutzt [1]."]
        elif model == "fake-empty":
            parts = []
        elif model == "fake-length":
            parts = ["Abgeschnittene ", "Antw"]
        elif model == "fake-drip":
            parts = [f"Stueck {i}. " for i in range(6)]
        try:
            if model == "fake-slow":
                time.sleep(8)
            if model == "fake-reason":
                piece(thinking="Ich überlege geheim ")
                piece(thinking="noch etwas.")
                parts = ["Antwort nach dem ", "Nachdenken [1]."]
            for i, p in enumerate(parts):
                if model == "fake-drip":
                    time.sleep(2)
                piece(p)
                if model == "fake-hang" and i == 0:
                    time.sleep(8)
                time.sleep(0.02)
            reason = "length" if model == "fake-length" else "stop"
            if ollama:
                send(json.dumps({"model": model, "message": {"role": "assistant", "content": ""}, "done": True, "done_reason": reason}) + "\n")
            else:
                send("data: " + json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]}) + "\n\n")
                send("data: [DONE]\n\n")
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1] if len(sys.argv) > 1 else 18290)), H)
    srv.daemon_threads = True
    srv.serve_forever()
