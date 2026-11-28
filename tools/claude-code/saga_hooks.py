#!/usr/bin/env python3
"""Saga-Gedaechtnis fuer Claude Code (2.6, Bugs-DB 2581, Anleitung docs/CLAUDE-CODE-HOOKS.md, Vertrag docs/MEMORY.md).

Ein Skript fuer zwei Hooks (nur Standardbibliothek, Python >= 3.9):
- SessionStart (startup, resume, clear, compact): GET /api/v1/memory/core -> Ich-Kern als additionalContext.
- UserPromptSubmit: Eingabe aus stdin ("prompt"), uebersprungen bei < 12 Zeichen oder /befehl. POST /api/v1/memory/recall
  mit dem Projekt aus SAGA_PROJEKT bzw. der Ordner-Zuordnung -> Treffer als additionalContext; ohne Treffer nichts.
Ausgabe: {"hookSpecificOutput": {"hookEventName": ..., "additionalContext": "<saga-data-Rahmen vom Server>"}}.

Nie blockieren: Gesamtzeit hoechstens 1,5 s (Einstellung "timeout"), jeder Fehler -> still exit 0 (eine Zeile ohne Inhalte
in ~/.cache/saga-hooks.log). Zugang: Adresse aus der Einstellung, sga_-Token mit Bereich "memory" aus dem macOS-
Schluesselbund (security find-generic-password) oder aus einer Datei mit chmod 600 - nie aus settings.json, nie ausgegeben.

Einstellung ~/.config/saga/hooks.json (anderer Ort: SAGA_HOOKS_CONFIG):
  {"url": "https://saga.example.org",
   "keychain": {"service": "saga-hooks", "account": "default"},   (oder "token_file": "~/.config/saga/token")
   "projects": {"~/src/saga": "Saga", "~/src/homelab": "Homelab"},
   "limit": 5, "timeout": 1.5, "min_chars": 12}
Umgebung: SAGA_PROJEKT (geht vor der Zuordnung), SAGA_URL (geht vor "url"), SAGA_TOKEN_FILE (geht vor Schluesselbund/Datei).
Aufruf von Hand zum Pruefen: echo '{"hook_event_name":"UserPromptSubmit","prompt":"Wie war das mit ...?"}' | python3 saga_hooks.py"""
import json
import os
import ssl
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

VERSION = "1"
LOG = os.path.expanduser("~/.cache/saga-hooks.log")
LOG_MAX = 256 * 1024
CONFIG = os.path.expanduser(os.environ.get("SAGA_HOOKS_CONFIG") or "~/.config/saga/hooks.json")
EVENTS = ("SessionStart", "UserPromptSubmit")
TEXT_MAX = 4000
_T0 = time.monotonic()
_DONE = threading.Event()                # Ausgabe beginnt: der Waechter beendet nicht mehr mitten im JSON


def log(event, what):
    """Eine Zeile ohne Inhalte (nie Token, Eingabe oder Erinnerungen)."""
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        if os.path.exists(LOG) and os.path.getsize(LOG) > LOG_MAX:
            os.replace(LOG, LOG + ".1")
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {event or '-'} {what} ({(time.monotonic() - _T0) * 1000:.0f} ms)\n")
    except OSError:
        pass


def load_config():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            c = json.load(f)
        return c if isinstance(c, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        log(None, "Einstellung unlesbar")
        return {}


def _file_token(path):
    path = os.path.expanduser(path)
    st = os.stat(path)
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise PermissionError("Token-Datei nicht chmod 600")
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def token(cfg, budget):
    if os.environ.get("SAGA_TOKEN_FILE"):
        return _file_token(os.environ["SAGA_TOKEN_FILE"])
    kc = cfg.get("keychain")
    if isinstance(kc, dict) and sys.platform == "darwin":
        r = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", str(kc.get("service") or "saga-hooks"),
                            "-a", str(kc.get("account") or "default"), "-w"], capture_output=True, text=True,
                           timeout=max(0.1, min(0.8, budget)))
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    if cfg.get("token_file"):
        return _file_token(cfg["token_file"])
    raise LookupError("kein Token")


def project(cfg, cwd):
    p = os.environ.get("SAGA_PROJEKT")
    if p and p.strip():
        return p.strip()
    m = cfg.get("projects")
    if not isinstance(m, dict) or not cwd:
        return None
    cwd = os.path.realpath(os.path.expanduser(cwd))
    best, name = -1, None
    for k, v in m.items():
        if not isinstance(v, str) or not isinstance(k, str):
            continue
        root = os.path.realpath(os.path.expanduser(k))
        if (cwd == root or cwd.startswith(root.rstrip(os.sep) + os.sep)) and len(root) > best:
            best, name = len(root), v.strip() or None
    return name


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Keine Weiterleitungen: urllib gaebe den Authorization-Header sonst an jedes Ziel weiter (auch an einen anderen Host,
    z. B. die Anmeldeseite eines vorgeschalteten Proxys, oder unverschluesselt per http). 3xx -> HTTPError -> still exit 0."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def plain_remote(url):
    """http:// zu etwas anderem als diesem Rechner (localhost, 127.0.0.0/8, ::1)?"""
    from urllib.parse import urlparse
    try:
        pu = urlparse(url)
        host = (pu.hostname or "").lower()
    except ValueError:
        return False
    return pu.scheme == "http" and not (host in ("localhost", "::1") or host.startswith("127."))


def call(url, tok, method, path, body, budget):
    data = json.dumps(body).encode() if body is not None else None
    rq = urllib.request.Request(url.rstrip("/") + path, data=data, method=method, headers={
        "Authorization": "Bearer " + tok, "Accept": "application/json", "Content-Type": "application/json",
        "User-Agent": "saga-hooks/" + VERSION})
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    with opener.open(rq, timeout=max(0.05, budget)) as r:
        return json.loads(r.read(4 * 1024 * 1024) or b"{}")


def run(inp):
    cfg = load_config()
    limit = float(cfg.get("timeout") or 1.5)
    ev = inp.get("hook_event_name") or inp.get("hookEventName")
    if ev not in EVENTS:
        return None, ev, "anderes Ereignis"
    url = os.environ.get("SAGA_URL") or cfg.get("url")
    if not url:
        return None, ev, "keine Adresse"
    if plain_remote(url):
        log(ev, "Warnung: http ohne TLS zu einem anderen Rechner - Token und Eingaben gehen im Klartext (https nehmen)")
    budget = lambda: limit - (time.monotonic() - _T0) - 0.05          # noqa: E731
    if ev == "UserPromptSubmit":
        prompt = inp.get("prompt")
        if not isinstance(prompt, str) or len(prompt.strip()) < int(cfg.get("min_chars") or 12) or prompt.lstrip().startswith("/"):
            return None, ev, "uebersprungen"
    tok = token(cfg, budget())
    if ev == "SessionStart":
        d = call(url, tok, "GET", "/api/v1/memory/core", None, budget())
    else:
        b = {"text": prompt.strip()[:TEXT_MAX], "limit": max(1, min(5, int(cfg.get("limit") or 5)))}
        pj = project(cfg, inp.get("cwd") or os.getcwd())
        if pj:
            b["projekt"] = pj
        d = call(url, tok, "POST", "/api/v1/memory/recall", b, budget())
    ctx = d.get("kontext") if isinstance(d, dict) else None
    if not isinstance(ctx, str) or not ctx.strip():
        return None, ev, "keine Treffer"
    n = len(d.get("treffer") or ()) if ev == "UserPromptSubmit" else 1
    return {"hookSpecificOutput": {"hookEventName": ev, "additionalContext": ctx}}, ev, f"ok {n}"


def _watchdog(limit):
    """Harte Grenze (haengendes DNS, langsamer Schluesselbund): still beenden, nichts ausgeben."""
    time.sleep(limit)
    if _DONE.is_set():
        return
    log(None, "Zeitlimit")
    os._exit(0)


def main():
    ev = None
    try:
        lim = float(load_config().get("timeout") or 1.5)
        threading.Thread(target=_watchdog, args=(max(0.2, min(10.0, lim)),), daemon=True).start()
        raw = sys.stdin.read(1024 * 1024)
        inp = json.loads(raw) if raw.strip() else {}
        if not isinstance(inp, dict):
            inp = {}
        ev = inp.get("hook_event_name")
        out, ev, what = run(inp)
        _DONE.set()
        if out is not None:
            sys.stdout.write(json.dumps(out, ensure_ascii=False))
            sys.stdout.flush()
        log(ev, what)
    except urllib.error.HTTPError as ex:
        log(ev, f"HTTP {ex.code}")
    except Exception as ex:  # noqa: BLE001 - nie blockieren: jeder Fehler -> still exit 0
        log(ev, type(ex).__name__)
    os._exit(0)


if __name__ == "__main__":
    main()
