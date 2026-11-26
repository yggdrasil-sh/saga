# Testumgebung (saga-test)

Für Server-Reihen, die mehr brauchen als einen einzelnen Server: LDAP/SSO, zwei verbundene Instanzen, ein echter
Knoten mit Zertifikat, alte Anruf-Clients. Läuft als root auf der Testmaschine; jede Reihe in einem eigenen
Netz-Namensraum (nur `lo` + Attrappe `um0`, kein Weg nach außen) mit eigener DB und eigenem `/etc/hosts` (nur im
Mount-Namensraum des Laufs). Nichts wird dauerhaft gestartet oder systemweit eingerichtet.

```sh
source /srv/saga-test/env.sh
tools/testumgebung/umgebung.sh einrichten                       # einmal (idempotent)
tools/testumgebung/umgebung.sh lauf sso 0 sso_ldap.py           # Art, Slot 0–8, Reihe (mit oder ohne tests/)
tools/testumgebung/umgebung.sh lauf fed 1 browser_fed_chat.js
MESH=1 tools/testumgebung/umgebung.sh lauf knoten 2 browser_horn_pingpong.js mesh   # Tag für eigenes Log
tools/testumgebung/umgebung.sh stop 2                           # Reste beenden (nur PIDs im Namensraum)
```

| Art | Was läuft | Reihen |
|---|---|---|
| `server` | ein Server; `SAGA_CALLS_E0`/`SAGA_CALLS23` aus `$UMG/e0/` | `browser_screen2.js`, `haertung.py`, `kalender_api.py`, `mcp.py`, `mcp_nach.py` |
| `sso` | dazu LDAP-Attrappe `ldap.test:389` (StartTLS, ldaptor) und OIDC-Attrappe `mockoidc:8080` | `sso_ldap.py`, `avatare.py` |
| `knoten` | Server hinter saga-media mit eingebautem Knoten (`calls.horn` + `horn_all`), Zertifikat der Test-CA | `browser_horn_uebernahme.js`, `browser_anruf_umzug.js`, `browser_horn_pingpong.js` |
| `fed` | zwei verbundene Instanzen A/B (`zwei_stacks.py`, Gerüst aus `tests/foederation2.py`) | `browser_fed.js`, `browser_fed_chat.js`, `browser_fed_share.js` |
| `fedknoten` | wie `fed`; A hinter saga-media mit eingebautem Knoten (`horn` + `horn_all`, Knoten mit B geteilt), B `horn` ohne `horn_all` | `browser_fed_horn_dm.js` |

- **Ein Slot = ein Lauf zur Zeit:** Namensraum `um<slot>`, DB `saga_umg_<slot>`, `run/<slot>` und Ports gehören dem Slot.
  Ein zweiter Lauf auf einem belegten Slot bricht mit „Slot … ist belegt“ ab (Sperre `/run/lock/saga-umgebung-um<slot>.lock`,
  auch über verschiedene `UMG`-Ordner hinweg; dazu je `$UMG/run/<slot>` und je DB, damit auch ein Lauf mit anderem `UMG_NS`
  wie `tools/handbuch-stack.sh` dem Slot nichts löscht); parallel also verschiedene Slots nehmen. Die Sperre fällt mit dem Lauf.
- In allen Arten läuft **Radicale** (CalDAV) im Namensraum auf `127.0.0.1:5232` (Konto `test`, nur Test-Passwort wie in
  den Reihen), `HK_TEST_CALDAV` ist gesetzt; dann sind fehlende Kalender in `mcp.py`/`mcp_nach.py` ein Fehler, ohne
  `HK_TEST_CALDAV` nur `SKIP`. Aus mit `UMG_CALDAV=0`. Braucht Radicale im System-Python (`/usr/bin/python3 -m radicale`; anderes mit
  `UMG_RADICALE=<befehl>`).
- **„Erste Schritte“** ist für alle Konten des Laufs schon mit „Nein“ beantwortet – auch für Konten, die eine Reihe selbst
  anlegt (Trigger in der DB des Laufs, `konten.py`), sonst liegt der Dialog `.hk-ask` über Anruf-Knöpfen und
  Chat-Einrichtung (z. B. `page.click #ct-call-v` läuft in `browser_horn_knoten.js`/`browser_horn_pegel.js` ab). Nicht bei
  `erste_schritte.py` und `browser_leere_zustaende.js` (prüfen den Dialog); von Hand aus mit `UMG_FIRST_STEPS=frage`.
- **Variablen je Reihe** setzt `innen.sh` selbst (an Server und Reihe; `UMG_SENV` gewinnt):

  | Reihe | Variablen |
  |---|---|
  | `erwaehnungen.py` | `SAGA_MENTION_DELAY=1` |
  | `collab.py` | `COLLAB_MAT_DELAY=0.5 COLLAB_COMPACT_IDLE=3` |
  | `ki_web.py` | `SEARXNG_URL=http://127.0.0.1:18292` (Attrappe der Reihe) und ein Test-`SEARXNG_SECRET` – ohne Secret startet der Server nicht |
  | `webhooks.py` | `SAGA_WEBHOOK_DEBOUNCE=1 SAGA_WEBHOOK_RETRY_SCALE=0.02` |
  | `versionen.py` | Backups vor Migrationen an (sonst immer `SAGA_SKIP_BACKUP=1`; von Hand `UMG_BACKUP=1`) |
  | `mcp_oauth.py`, `mcp_oauth_proben.py` | `SAGA_OAUTH_REG_PER_IP=40 SAGA_OAUTH_REG_PER_HOUR=1000`; anonyme Anfragen mit dem Server-Standard (`SAGA_OAUTH_ANON_PER_IP` 30) |
  | `fakerelay.py` | `SAGA_PUSH_RELAY_URL=http://127.0.0.1:18630 SAGA_PUSH_RELAY_HOST=saga.example.org` (Relais der Reihe im Namensraum) |
  | `widget_push.py` | wie `fakerelay.py`, dazu `SAGA_WIDGET_PUSH_GAP=6` |
  | `relay_dienst.py` | keine; die Reihe läuft ohne `tests/pgshim` (der Relais-Dienst im Prozess braucht echtes SQLite) |

  Eigene Sonder-Server von Hand: dieselben Variablen in `UMG_SENV` (Server) **und** in der Umgebung des Aufrufs (Reihe) –
  die Reihen lesen sie selbst (z. B. `GAP`, `FR_PORT`, Grenzen).

  Startet der Server nicht, läuft die Reihe nicht: im Log „SERVER NICHT BEREIT“ mit dem Ende von `server.log`, `rc=98`.
- **Knoten-Binaries:** `UMG_BIN` (Standard `$UMG/bin-<rev>`, `<rev>` = Commit des Worktrees). `lauf` baut sie für
  `knoten`/`fedknoten`, wenn sie fehlen (nach jedem neuen Commit einmal, ~1 min); andere Arten bekommen
  `GJALLARHORN_BIN`/`SAGA_MEDIA_BIN` nur, wenn es sie schon gibt (sonst bauen Reihen wie `a3_tunnel.py` selbst). Eigener
  Stand: `UMG_BIN=<ordner>` mit `gjallarhorn` und `saga-media`.
- Ports je Slot: Server `9001+10·slot` (Admin +1, saga-media intern +2, Knoten +3, Föderation +4…+9), Hilfsports
  `19001+10·slot` (Gravatar-Attrappe +3). Feste Ports der Reihen (8080, 389, 8094–8097 …) gibt es nur im Namensraum.
- `einrichten`: Test-CA unter `$UMG/ca/` (nur Test-Schlüssel, 30 Tage), NSS-DB in `$UMG/home` (Chromium vertraut der
  CA nur mit `HOME=$UMG/home`), ldaptor/twisted unter `$UMG/pylib`, `calls.js` aus E0 (`cfac8b11`) und v2.3.0 (voller
  Klon nötig), Ton-Datei, gjallarhorn/saga-media aus dem Worktree (`$UMG/bin-<rev>`); prüft `age`
  (`UMG_GOBIN`), Python-Playwright (`UMG_PWLIB`) und `pg_trgm` (Paket `postgresql16-contrib`).
- Ergebnis je Lauf: `$UMG/log/<reihe>[.<tag>].log`, Zeile in `$UMG/ergebnis.tsv` (rc, ok, fail, skip, Tracebacks, Dauer),
  Server-Log/Bilder unter `$UMG/run/<slot>/` (beim nächsten Lauf auf dem Slot gelöscht; eigener `UMG` oder `tag` je
  Rolle, wenn mehrere dieselbe Reihe laufen lassen).
- Variablen: `UMG` (Standard `/srv/saga-test/tmp/umgebung`), `UMG_BIN`, `UMG_GOBIN`, `UMG_PWLIB`, `UMG_NS` (`um`),
  `UMG_DB` (`saga_umg`), `UMG_LIMIT`, `UMG_SENV` (Server), `UMG_BACKUP`, `UMG_CALDAV`, `UMG_FIRST_STEPS`, `UMG_PYX`
  (PYTHONPATH der Reihe), `UMG_MESH_MAX` (Mesh-Grenze
  senken, z. B. `3` für `browser_meeting_gruppe.js` über `knoten` – sonst fällt die Vierer-Gruppe oft auf Mesh zurück, solange
  die Chat-Geräte ihre Fähigkeiten noch nicht gemeldet haben); alles andere geht an die Reihe.
- Abbrechen: den Aufruf beenden, dann `umgebung.sh stop <slot>` – beendet nur die Prozesse im Namensraum
  (auch `timeout`/Chromium in eigenen Prozessgruppen); ein neuer Lauf auf demselben Slot räumt ebenso vorher auf.
- Weitere Reihen mit den Werkzeugen von saga-test: `backup_extern.py`/`backup_persoenlich.py` (`age` aus `UMG_GOBIN`
  im PATH), `browser_geraetegrenze.py` mit `UMG_PYX=$UMG_PWLIB`, Suche mit `pg_trgm` (Server-Log ohne
  „Suche ohne Trigramm-Index“, Zeile am Ende des Logs).
