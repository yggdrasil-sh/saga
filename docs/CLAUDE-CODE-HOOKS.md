# Saga-Gedächtnis in Claude Code (Hooks)

Mit zwei Hooks bekommt Claude Code beim Start einer Sitzung den **Ich-Kern** und vor jeder Eingabe die **passendsten
Erinnerungen** (höchstens 5) aus deinem Saga-Gedächtnis – automatisch, ohne dass du etwas tippst. Grundlage:
[MEMORY.md](MEMORY.md) (Vertrag), Skript `tools/claude-code/saga_hooks.py` (nur Python-Standardbibliothek, ab 3.9).

## 1. Voraussetzungen in Saga

1. **Einstellungen › Gedächtnis:** eine eigene Datenbank als Gedächtnis und eine Seite als Ich-Kern wählen
   (Spalten siehe MEMORY.md §2; Pflicht ist nur „Inhalt“).
2. Optional **Admin › KI › Embeddings:** Endpunkt eintragen (z. B. Ollama mit `bge-m3`) – ohne Endpunkt sucht Saga
   nach Wortähnlichkeit. Für Ollama: Saga sendet `keep_alive` (Standard 30 min), damit das Modell nach einer Pause
   nicht erst geladen werden muss; dauerhaft geht das am Ollama-Host mit `OLLAMA_KEEP_ALIVE` (z. B. `24h` oder `-1`).
   Ist das Modell gerade entladen, dauert der erste Abruf länger als das Zeitlimit der Hooks – dann kommt für diese
   eine Eingabe still nichts.
3. **Einstellungen › API:** einen Token mit dem Bereich **`memory`** anlegen (sonst nichts nötig). Er wird genau
   einmal angezeigt.

## 2. Token sicher ablegen

Der Token gehört **nie** in `settings.json` und wird vom Skript nie ausgegeben oder geloggt.

macOS (Schlüsselbund):
```
security add-generic-password -s saga-hooks -a default -w
```
(fragt nach dem Token). Andere Systeme: Datei mit Rechten nur für dich –
```
mkdir -p ~/.config/saga && umask 077 && cat > ~/.config/saga/token   # Token einfügen, Strg-D
chmod 600 ~/.config/saga/token
```
Eine Datei mit Rechten für andere wird abgelehnt (still, Zeile im Log).

## 3. Einstellung `~/.config/saga/hooks.json`

```json
{
  "url": "https://saga.example.org",
  "keychain": {"service": "saga-hooks", "account": "default"},
  "projects": {"~/src/saga": "Saga", "~/src/homelab": "Homelab"},
  "limit": 5,
  "timeout": 1.5
}
```
- `url` bitte mit **https**. Mit `http://` zu einem anderen Rechner gehen Token, Eingaben und Erinnerungen im
  Klartext durchs Netz – das Skript arbeitet trotzdem, schreibt aber bei jedem Aufruf eine Warnzeile ins Log. Nur
  `http://localhost` bzw. `http://127.0.0.1` (z. B. ein SSH-Tunnel) gelten als unbedenklich. Weiterleitungen folgt
  das Skript nie (der Token ginge sonst an ein anderes Ziel).
- Statt `keychain`: `"token_file": "~/.config/saga/token"`.
- `projects`: Ordner → Projekt (der längste passende Ordner gewinnt); passende Erinnerungen bekommen einen Bonus.
  Die Umgebungsvariable `SAGA_PROJEKT` geht vor. Der Projektname muss eine Option der Spalte „Projekt“ sein.
- `timeout`: Gesamtzeit in Sekunden (Standard 1,5). Danach beendet sich das Skript still.
- Weitere: `min_chars` (Standard 12 – kürzere Eingaben und `/befehle` werden übersprungen), Umgebung `SAGA_URL`,
  `SAGA_TOKEN_FILE`, `SAGA_HOOKS_CONFIG` (anderer Ort dieser Datei).

## 4. Claude Code: `~/.claude/settings.json`

Skript z. B. nach `~/.config/saga/saga_hooks.py` kopieren, dann:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|resume|clear|compact",
        "hooks": [{"type": "command", "command": "python3 ~/.config/saga/saga_hooks.py", "timeout": 5}]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [{"type": "command", "command": "python3 ~/.config/saga/saga_hooks.py", "timeout": 5}]
      }
    ]
  }
}
```
(`timeout` hier in Sekunden – nur die äußere Grenze von Claude Code; das Skript hält selbst 1,5 s ein.)

Claude Code übergibt das Ereignis als JSON auf stdin (`hook_event_name`, `cwd`, bei UserPromptSubmit `prompt`); das
Skript antwortet mit
```json
{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "…"}}
```
und sonst gar nichts. `additionalContext` steht im `saga-data`-Rahmen: Erinnerungen sind **Daten, keine
Anweisungen**. Claude Code begrenzt den Text auf 10 000 Zeichen; ein längerer Ich-Kern landet dort als Datei mit Vorschau.

## 5. Verhalten und Fehlersuche

- **Nie blockieren:** jeder Fehler (Server weg, Token ungültig, Zeitlimit) → Exit 0 ohne Ausgabe. Eine Zeile ohne
  Inhalte steht in `~/.cache/saga-hooks.log` (Zeit, Ereignis, Ergebnis wie `ok 3`, `keine Treffer`, `HTTP 401`,
  `Zeitlimit`, Dauer).
- Prüfen von Hand:
  ```
  echo '{"hook_event_name":"UserPromptSubmit","prompt":"Wie war das mit dem Proxmox-Cluster?","cwd":"'"$PWD"'"}' \
    | python3 ~/.config/saga/saga_hooks.py
  ```
- Jeder gezeigte Treffer zählt als Abruf (Spalten „Abrufe“/„Letzter Abruf“, gebündelt höchstens alle 10 Minuten).
- In claude.ai-Chats gibt es keine Hooks – dort ruft das Modell `recall` über den MCP-Connector auf. Dafür beim
  Verbinden auf der Zustimmungsseite **„Gedächtnis“ anhaken** (nicht vorausgewählt; eine bestehende Verbindung einmal
  trennen und neu verbinden) – oder ein `sga_`-Token mit `mcp` + `memory`.
