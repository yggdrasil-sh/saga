# Saga 2.5 – upgrading from 2.3/2.4 (short version)

For admins running Saga with **docker compose** behind a reverse proxy that terminates TLS itself – mainly **Nginx
Proxy Manager (NPM)**. The full German version with more detail: [../UPGRADE-2.5.md](../UPGRADE-2.5.md). Details of
every change: [CHANGELOG](../../CHANGELOG.md), full guide: [GUIDE.md](GUIDE.md).

## 1. Before

- **Check versions:** `curl -s https://saga.example.org/api/version` – `schema` is **9** on 2.3, **14** on 2.4.
  Saga 2.5.0 raises it to **35** (later 2.5.x or 2.6 versions may add migrations – `schema` always shows the current
  value). 2.3 can go straight to 2.5; also read "From 2.3 to 2.4" in the [guide](GUIDE.md).
- **Backup:** Saga dumps the database itself before migrating (`/data/backups/…-vor-schema-35.dump`). Make one by
  hand as well: `docker compose exec saga python run.py backup --dateien` (database plus page files), and keep the
  `.env` (`SECRET_KEY`).
- **No downgrade:** after the migrations 2.4 refuses to start on this database. The only way back is the backup
  from before the update (section 6).

## 2. Update

```sh
docker compose pull && docker compose up -d
docker compose logs saga | grep saga:     # backup and migrations
curl -s http://127.0.0.1:8080/healthz     # "ok"
```

`SAGA_VERSION=2` gets 2.5 once 2.5.0 is released; `testing` (= `rc`) gets the release candidates now.
[`tools/saga-testing-einrichten.sh`](../../tools/saga-testing-einrichten.sh) switches the channel, backs up `.env` and
the database and installs a systemd timer that pulls every 15 minutes and restarts only on a new image
(`sudo sh saga-testing-einrichten.sh /path/to/saga testing`; log: `journalctl -t saga-autoupdate`). Behind NPM
nothing changes in `docker-compose.yml` or `.env`: Saga keeps listening on 8080.

## 3. Reverse proxy

**One domain, port 443 only, pass WebSocket through** – no TLS passthrough, no second name for the call node, no
extra ports (older notes about `horn-0…`, port 8443 or SNI routing do not apply to this setup).

- **NPM:** proxy host → forward to Saga port 8080, **"Websockets Support" on (required)**. Without it
  `/api/sfu/ws` answers **426** and calls through the node fail. Optional under *Advanced*:
  `proxy_read_timeout 1h; proxy_send_timeout 1h;` (idle push WebSocket of the Android app).
- **Traefik, Caddy:** nothing to do. **nginx:** in `location /` add `proxy_http_version 1.1;`,
  `proxy_set_header Upgrade $http_upgrade;`, `proxy_set_header Connection $http_connection;`.
- **Cloudflare (orange cloud):** passes WebSocket, but permanent media traffic may violate their terms of service
  (especially on the free plan).
- `BASE_URL` = the https address with a DNS name, `TRUSTED_PROXIES` = the proxy.
- Optional, not required: UDP 443 for the node (`HORN_UDP_LISTEN=:443`, `"443:443/udp"`).

Check: Admin → **Calls** → "Calls through the Saga address (tunnel)" → "Does the proxy pass WebSocket through?" →
**Check now**, or with `curl` (`101` = fine, `426` = enable "Websockets Support"):

```sh
curl -s -o /dev/null -w '%{http_code}\n' --max-time 5 --http1.1 \
  -H 'Connection: Upgrade' -H 'Upgrade: websocket' -H 'Sec-WebSocket-Version: 13' \
  -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' -H 'Sec-WebSocket-Protocol: saga-tunnel.1' \
  https://saga.example.org/api/sfu/ws
```

## 4. Admin area after the update

- **Calls:** Gjallarhorn is **experimental** in 2.5 and **off** by default. Turn on "Large group calls via
  Gjallarhorn", keep "Use built-in node for calls" on – behind NPM it runs in **tunnel mode** (no own port, no own
  certificate). "All calls via nodes (including 1:1)" is **on** by default; a stored "off" from before 2.5 counts as
  unset once – untick and save if you want direct 1:1 calls. Then "Check now" and **"Sync capabilities now"** (asks
  all connected servers right away instead of within 6 hours; per server also under Federation).
- **General → Chats:** "Modern chats (level 3) – on by default"; existing chats switch once all active app devices
  run 2.5, at the latest after "Convert existing chats at the latest after (days)" (`chat_v3_days`, default 90). If General was saved before, the switch may be stored
  as off – turn it on deliberately.
- **General → Pages:** "Page editor" "Default (Tiptap)" (the update switches once); "Live simultaneous editing (stage 2, test)"
  (`notes.live`) is off by default and needs WebSocket. Gravatar is unchanged since 2.4 (per-person opt-in).
- **Diagnostics** (new tab): error reports, "Send diagnostics" (`D-…` IDs), editor and call diagnostics.

## 5. Apps

Calls and the new chat features need **apps 2.5** ([Saga apps](https://github.com/yggdrasil-sh/saga-apps/releases)):
Android APK `Saga-2.5.0.apk` from the release (release candidates: from the pre-release `v<version>-rc.N`); iPhone/iPad only via self-distribution (e.g. MDM)
or as a web app (Safari does not use the frame path yet, so calls from it run direct or via UDP/TURN-TLS). Apps 2.4
still sign in and work with pages, databases and chats; 1:1 calls with an old active device fall back to direct
(with a note that the other side is outdated), groups up to 4 go direct with a note, old apps cannot join larger
groups or calls on the tunnel node (they say Saga 2.5 is needed). Devices unused for more than 30 days are ignored.

## 6. Troubleshooting and way back

- **426:** the proxy does not pass the WebSocket upgrade – NPM "Websockets Support", nginx upgrade headers.
- **401 in federation** (`sig.rejected`/`sig.replay` in the audit log): clocks more than 5 minutes apart, a proxy that
  rewrites path or query, `BASE_URL` not the name the other server connected to, or a self-built image with a newer
  Go before the saga-media fix (Bugs-DB 2182). Then "Sync capabilities now".
- **Call does not connect:** check the node and the WebSocket check under Calls, old apps, Safari; then both sides
  **Settings → Help → Send diagnostics** right after the attempt and look up the `D-…` ID under Admin → Diagnostics.
- **Back to 2.4:** with 2.5 still configured, `docker compose stop saga`, then
  `docker compose run --rm saga python run.py restore /data/backups/<…-vor-schema-35.dump>`, then set
  `SAGA_VERSION=2.4` and `docker compose pull && docker compose up -d`. Disable the auto-update timer first if you
  installed it.
