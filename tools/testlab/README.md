# Saga-Testlabor (Hetzner Cloud + Cloudflare)

Drei kurzlebige, stündlich abgerechnete Server für realistische Tests von Anrufen, Knoten und Föderation.
Alles trägt das Label `saga-test` (DNS-Einträge den Kommentar `saga-test`) und wird nach jeder Runde komplett gelöscht.

| Rolle | Aufbau | Namen |
|---|---|---|
| **A** | Saga hinter Traefik, eingebauter Knoten per TLS-Durchreiche (SNI, PROXY v2, nur TCP) | `a.`, `horn-a.` |
| **B** | Saga direkt auf 443 (Caddy terminiert TLS, keine SNI-Weiche), kein eingebauter Knoten | `b.` |
| **C** | externer Knoten für B (geteilt) und Testclients: Headless-Chromium mit Fake-Kamera, Node + playwright-core, `saga-test-ns` (NAT-Namespaces, netem, UDP aus) | `horn-b.` |

ARM (CAX) und amd64 dürfen gemischt werden (`TESTLAB_TYPES`).

## Voraussetzungen

- Eigenes Hetzner-Projekt nur für Tests, API-Token mit Lese-/Schreibrecht → `HCLOUD_TOKEN`
- Cloudflare-Token nur mit DNS-Bearbeitung der Test-Zone → `CF_API_TOKEN`, Zone → `TESTLAB_DOMAIN`
- Eigener Test-Schlüssel: `ssh-keygen -t ed25519 -N '' -C saga-test -f ~/.ssh/saga-test`
- Tokens nur in der Umgebung der Shell setzen, nie in Dateien im Repo. cloud-init enthält keine Geheimnisse:
  Saga-Passwörter und Schlüssel entstehen auf dem Server (`/root/saga-test/zugang.txt`, nur root).

## Ablauf einer Runde

```sh
python3 tools/testlab/testlab.py plan      # Kosten je Stunde ansehen
python3 tools/testlab/testlab.py up        # anlegen, DNS, warten auf /var/lib/saga-test/ready
python3 tools/testlab/testlab.py koppeln   # Schalter auf A/B, Knoten C an B koppeln (Code geht direkt B -> C)
# ... Tests ...
python3 tools/testlab/testlab.py down      # alles mit dem Label löschen, Rest auflisten
```

Admin-Bereich per Tunnel: `ssh -i ~/.ssh/saga-test -L 8081:127.0.0.1:8081 root@a.<zone>` → http://localhost:8081.
Föderation A ↔ B (Einladungscode) und „Teilen an“ richtet man dort ein, siehe `docs/FELDTEST-GJALLARHORN.md`.

`koppeln` nutzt zwei Befehle von Saga (ab 2.5.0-rc.123):

- `python run.py einstellen calls.horn=1 calls.horn_all=1 calls.horn_from=3 chat_v3=1 …` – Schalter ohne Oberfläche
- `echo <passwort> | python run.py benutzer-anlegen c1 c2 …` – Testkonten für die Clients (Passwort nur über stdin)
- `python run.py horn-koppeln <name> [--teilen] [--ohne-udp]` – wie Admin → Anrufe → Erweitern, gibt das Docker-Skript aus

## Testclients auf C

```sh
saga-test-ns add 1                         # Namespace c1 hinter NAT
saga-test-ns netem 1 delay 80ms 20ms loss 2%
saga-test-ns udp 1 aus                     # nur TCP/TLS (TURN über 443, Knoten-TLS)
saga-test-ns run 1 chromium --headless=new --no-sandbox --use-fake-device-for-media-stream --use-fake-ui-for-media-stream …
saga-test-ns alle-weg
```

## Geplante Fälle

Föderation (Kaskade, Teilen, ohne Knoten, 1:1 über Servergrenzen), 6–12 Clients, netem / UDP aus / Knotenausfall,
Updates und 2.4 → 2.5.

## Offen beim ersten Lauf prüfen

- Zertifikat des eingebauten Knotens auf A hinter der Durchreiche (ALPN-01 über Traefik)
- IPv6: AAAA-Einträge werden gesetzt; ob die Knoten sie melden (siehe Punkt 6 im Status)
