# Saga-Push-Relais (`relay/`)

Kleiner eigener Dienst (Saga 2.6, Bugs-DB 2262): Saga-Server ohne eigenen Apple-Schlüssel schicken ihre Mitteilungen an
die iOS-/iPadOS-App über dieses Relais. Der Betreiber des Relais hat den APNs-Schlüssel (`.p8`) des Apple-Kontos, das
die App veröffentlicht. Die Inhalte sind zwischen Saga-Server und App **Ende-zu-Ende verschlüsselt** – das Relais sieht
nur Steuerdaten (Gerätetoken, Topic, Push-Typ, Priorität, Ablaufzeit, undurchsichtige collapse-id, feste Loc-Keys).

Verbindlicher Vertrag (Protokoll, Felder, Fehler, Ratenstufen): [`docs/PUSH-RELAY.md`](../docs/PUSH-RELAY.md).

## Aufbau

| Datei | Aufgabe |
|---|---|
| `sagarelay/app.py` | Endpunkte `/`, `/v1/info`, `/v1/servers`, `/v1/servers/me`, `/v1/servers/me/rotate`, `/v1/push`; Prüfung der Anfragen |
| `sagarelay/auth.py` | Signaturprüfung (RFC 9421 über `saga/fedsig.py`, Tag `saga-relay/1`), Replay-Schutz, Rotations-Nachweis |
| `sagarelay/apns.py` | HTTP/2 zu Apple, ES256-Anbieter-Token je Team, Erneuerung bei `ExpiredProviderToken`, Fehlerabbildung |
| `sagarelay/limits.py` | Token-Bucket je Server und Token (`ring`/`alert`/`bg`), Tagesdeckel, Anmelde-Bremse |
| `sagarelay/verify.py` | Domain-Nachweis `https://<host>/.well-known/saga-push-relay` mit SSRF-Schutz |
| `sagarelay/store.py` | SQLite: `servers`, `nonces` (10 min), `daily_stats` (30 Tage) – keine Token, keine Inhalte |
| `sagarelay/log.py` | eine JSON-Zeile je Anfrage auf stdout, nie Token/Nutzlast/IP |
| `relayctl.py` | Verwaltung: `list, show, approve, block, unblock, stage, limits, stats` |

Aus `saga/` wird nur `fedsig.py` benutzt (im Image unter `/app/saga/fedsig.py`). Der Dienst läuft als **ein** Prozess
(Ratenzähler im Speicher) – nicht mit mehreren Workern oder mehreren Repliken betreiben.

## Konfiguration

`RELAY_CONFIG` (Standard `/data/relay.json`, Vorlage [`deploy/relay.example.json`](deploy/relay.example.json)):

| Schlüssel | Bedeutung |
|---|---|
| `teams[]` | `team_id`, `key_id` (je 10 Zeichen), `p8_file` (Pfad zum `.p8`, relativ zur Konfiguration oder absolut), `bundles` (Bundle-IDs **B**), `widgets` (Topic `B.push-type.widgets` erlauben, Standard aus) |
| `registration` | `verify` (Domain-Nachweis schaltet frei, sonst `pending`) oder `approve` (immer von Hand) |
| `sandbox` | Umgebung `sandbox` (Entwicklungs-Builds) erlauben, Standard an |
| `public_host` | öffentlicher Name des Relais, gegen den `@authority` der Signatur geprüft wird – **im Betrieb setzen** |
| `trust_proxy` | Client-IP (Anmelde-Bremse) aus dem letzten Eintrag von `X-Forwarded-For` (hinter Traefik: an) |
| `tiers` | einzelne Werte der Stufen `neu`/`aktiv` überschreiben (`per_min`, `per_token_min`, `per_day` je `ring`/`alert`/`bg`) |
| `reg_per_ip_hour` | neue Anmeldungen je IP und Stunde (Standard 5) |
| `apns_concurrency`, `ring_slots`, `slot_wait`, `apns_timeout` | gleichzeitige APNs-Anfragen (32), davon für Anrufe reserviert (4), Wartezeit auf einen Platz (2 s), Zeitgrenze zu Apple (10 s) |
| `operator`, `privacy_note` | Betreiber/Kontakt und zusätzlicher Text auf der Startseite `/` |

Umgebung: `RELAY_DB` (Standard `/data/relay.db`), `RELAY_LISTEN`/`RELAY_PORT` (`0.0.0.0:8080`). Nur für Tests:
`RELAY_APNS_URL` (statt Apple, http erlaubt) und `RELAY_VERIFY_TEST_BASE` (Domain-Nachweis über `http://127.0.0.1:<port>`).

## Betrieb

```sh
# Image (CI baut ghcr.io/<owner>/saga-relay:<version>, :testing-<commit>, :testing, :latest)
docker build -f relay/Dockerfile -t saga-relay .

# auf dem Host neben deploy/compose.yml
mkdir -p data secrets && cp relay.example.json data/relay.json   # anpassen
cp AuthKey_XXXXXXXXXX.p8 secrets/apns.p8 && chown -R 10001:10001 data secrets && chmod 600 secrets/apns.p8
RELAY_HOST=relay.example.org docker compose up -d
curl -s https://relay.example.org/v1/info

# Verwaltung
docker exec saga-relay python relayctl.py list [--state pending]
docker exec saga-relay python relayctl.py approve s_abcdefghijklmnop
docker exec saga-relay python relayctl.py block s_abcdefghijklmnop --note "Missbrauch"
docker exec saga-relay python relayctl.py limits s_abcdefghijklmnop '{"alert": {"per_min": 3000}}'
docker exec saga-relay python relayctl.py stats --days 7
```

`relayctl` schreibt in dieselbe SQLite-Datei; der Dienst liest den Server bei jeder Anfrage neu – Freischalten und
Sperren wirken sofort. Sperren betrifft auch neue Anmeldungen mit demselben Host (die landen in `pending`).

**Protokolle:** stdout, eine JSON-Zeile je Anfrage (`ts, ev, server_id, push_type, rate, prio, status, apns_reason, ms`,
bei Fehlern `tok_h`). Die Startseite verspricht 14 Tage Aufbewahrung: mit `json-file` begrenzt `compose.yml` die Größe
(`max-size`/`max-file`); wer genau 14 Tage braucht, nimmt `journald` mit `MaxRetentionSec=14d` oder leert die Protokolle
per Zeitplan.

**Sicherung:** `data/relay.db` (Server-Kennungen und öffentliche Schlüssel). Geht sie verloren, melden sich die Server
beim nächsten Versuch selbst neu an (neue `server_id`, Stufe `neu`).

## Tests

`tests/relay_dienst.py` startet das Relais im Prozess mit Fake-APNs und Fake-Domain-Nachweis (Ports ab
`RELAY_TEST_PORT`, Standard 18474):

```sh
PYTHONPATH=. python tests/relay_dienst.py      # Ende: "RELAY-DIENST n/n - ALLES GRUEN"
```

Ende-zu-Ende mit dem Saga-Server: `tests/fakerelay.py` und `tests/push_relais_weg.py` (starten dieses Relais ebenfalls).
