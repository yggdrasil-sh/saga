# Testumgebung (saga-test)

Für Server-Reihen, die mehr brauchen als einen einzelnen Server: LDAP/SSO, zwei verbundene Instanzen, ein echter
Knoten mit Zertifikat, alte Anruf-Clients. Läuft als root auf der Testmaschine; jede Reihe in einem eigenen
Netz-Namensraum (nur `lo` + Attrappe `um0`, kein Weg nach außen) mit eigener DB und eigenem `/etc/hosts` (nur im
Mount-Namensraum des Laufs). Nichts wird dauerhaft gestartet oder systemweit eingerichtet.

```sh
source /srv/saga-test/env.sh
tools/testumgebung/umgebung.sh einrichten                       # einmal (idempotent)
tools/testumgebung/umgebung.sh lauf sso 0 sso_ldap.py           # Art, Slot 0–8, Reihe
tools/testumgebung/umgebung.sh lauf fed 1 browser_fed_chat.js
MESH=1 tools/testumgebung/umgebung.sh lauf knoten 2 browser_horn_pingpong.js mesh   # Tag für eigenes Log
tools/testumgebung/umgebung.sh stop 2                           # Reste beenden (nur PIDs im Namensraum)
```

| Art | Was läuft | Reihen |
|---|---|---|
| `server` | ein Server; `SAGA_CALLS_E0`/`SAGA_CALLS23` aus `$UMG/e0/` | `browser_screen2.js` |
| `sso` | dazu LDAP-Attrappe `ldap.test:389` (StartTLS, ldaptor) und OIDC-Attrappe `mockoidc:8080` | `sso_ldap.py`, `avatare.py` |
| `knoten` | Server hinter saga-media mit eingebautem Knoten (`calls.horn` + `horn_all`), Zertifikat der Test-CA | `browser_horn_uebernahme.js`, `browser_anruf_umzug.js`, `browser_horn_pingpong.js` |
| `fed` | zwei verbundene Instanzen A/B (`zwei_stacks.py`, Gerüst aus `tests/foederation2.py`) | `browser_fed.js`, `browser_fed_chat.js`, `browser_fed_share.js` |
| `fedknoten` | wie `fed`; A hinter saga-media mit eingebautem Knoten (`horn` + `horn_all`, Knoten mit B geteilt), B `horn` ohne `horn_all` | `browser_fed_horn_dm.js` |

- Ports je Slot: Server `9001+10·slot` (Admin +1, saga-media intern +2, Knoten +3, Föderation +4…+9), Hilfsports
  `19001+10·slot` (Gravatar-Attrappe +3). Feste Ports der Reihen (8080, 389, 8094–8097 …) gibt es nur im Namensraum.
- `einrichten`: Test-CA unter `$UMG/ca/` (nur Test-Schlüssel, 30 Tage), NSS-DB in `$UMG/home` (Chromium vertraut der
  CA nur mit `HOME=$UMG/home`), ldaptor/twisted unter `$UMG/pylib`, `calls.js` aus E0 (`cfac8b11`) und v2.3.0 (voller
  Klon nötig), Ton-Datei, gjallarhorn/saga-media aus dem Worktree (`$UMG/bin-<rev>`); prüft `age`
  (`UMG_GOBIN`), Python-Playwright (`UMG_PWLIB`) und `pg_trgm` (Paket `postgresql16-contrib`).
- Ergebnis je Lauf: `$UMG/log/<reihe>[.<tag>].log`, Zeile in `$UMG/ergebnis.tsv`, Server-Log/Bilder unter `$UMG/run/<slot>/`.
- Variablen: `UMG` (Standard `/srv/saga-test/tmp/umgebung`), `UMG_BIN`, `UMG_GOBIN`, `UMG_PWLIB`, `UMG_NS` (`um`),
  `UMG_DB` (`saga_umg`), `UMG_LIMIT`, `UMG_SENV` (Server), `UMG_PYX` (PYTHONPATH der Reihe); alles andere geht an die Reihe.
- Abbrechen: den Aufruf beenden, dann `umgebung.sh stop <slot>` – beendet nur die Prozesse im Namensraum
  (auch `timeout`/Chromium in eigenen Prozessgruppen); ein neuer Lauf auf demselben Slot räumt ebenso vorher auf.
- Weitere Reihen mit den Werkzeugen von saga-test: `backup_extern.py`/`backup_persoenlich.py` (`age` aus `UMG_GOBIN`
  im PATH), `browser_geraetegrenze.py` mit `UMG_PYX=$UMG_PWLIB`, Suche mit `pg_trgm` (Server-Log ohne
  „Suche ohne Trigramm-Index“, Zeile am Ende des Logs).
