# Saga 2.6 – upgrading from 2.5 (short version)

For admins running Saga with **docker compose**, mainly behind **Nginx Proxy Manager (NPM)** or another reverse proxy
that terminates TLS itself. Coming from 2.3/2.4? Read [UPGRADE-2.5.md](UPGRADE-2.5.md) first – the migrations then run
straight through to 2.6. German version: [../UPGRADE-2.6.md](../UPGRADE-2.6.md). Details of every change:
[CHANGELOG](../../CHANGELOG.md) (in German), full guide: [GUIDE.md](GUIDE.md).

**In short:** make a backup, pull the image, restart – Saga upgrades the database itself. Nothing changes at the proxy.
The main news: **Gjallarhorn is on by default** – large group calls and calls between two people go through the
built-in node.

## 1. Before

- **Check the version:** at the bottom of the admin area or `curl -s https://saga.example.org/api/version` – `schema` is
  **35** on 2.5.0 (test versions from `main` may already be higher). Saga 2.6 raises it to **40**.
- **Backup:** Saga dumps the database itself before migrating (`/data/backups/…-vor-schema-40.dump`). Make one by hand
  as well: `docker compose exec saga python run.py backup --dateien`, and keep the `.env` (`SECRET_KEY`).
- **No downgrade:** after the migrations 2.5 refuses to start on this database ("Die Datenbank hat Schema-Stand 40,
  diese Saga-Version kennt nur 35", `SchemaTooNew`). The only way back is the backup (section 7).

## 2. Update

```sh
docker compose pull && docker compose up -d   # migrations 36 to 40 run at startup
docker compose logs saga | grep saga:         # backup and migrations
curl -s http://127.0.0.1:8080/healthz         # "ok"
```

With `SAGA_VERSION=2` (the default) 2.6 arrives by itself; if you pinned `2.5`, switch to `2.6` or `2`
([guide, "Updates"](GUIDE.md#updates)). The migrations run on their own. One of them (38, address book) fills the
contacts from the chats once on first start and holds the start until done – about 0.25 ms per chat contact, e.g.
47,000 in about 11 s.

## 3. Gjallarhorn on by default

From 2.6, **"Large group calls via Gjallarhorn"** (Admin › Calls) is on by default. That means:

- **An "off" saved in 2.5 also counts as on after the update** – as long as you don't deliberately set the switch from
  2.6 on. If you don't want Gjallarhorn, untick it after the update and save (or
  `docker compose exec saga python run.py einstellen calls.horn=0`). From then on it stays off.
- **Calls between two people (1:1) go through the built-in node** ("All calls via nodes (including 1:1)", on by
  default): devices don't see each other's IP addresses. Saga only connects directly as a fallback – if no node is ready
  or a device of the other person used in the last 30 days can't do it (e.g. an old app).
- **Limits per call** (upper limits, you can only lower them; fixed when the call starts): through the built-in node
  (tunnel/TCP) **16 with video, 50 audio only**; through a node with UDP (e.g. a paired one) **25 with video, 50 audio
  only**. If a node with UDP is ready (below 70 % load), a new call starts there. Directly (mesh) it stays at 4 with
  video / 6 audio only. Measurements: [guide, "Limits and measurements"](GUIDE.md#limits-and-measurements-gjallarhorn).
- The "Experimental" label is gone from the web and the admin area. Admin › Calls now shows the line **"Limits per call
  via nodes"** and per node **"Estimated capacity: about N with video / M audio only at the same time"**.

Paired nodes remain an add-on for more capacity; the built-in node is enough to start with.

## 4. Reverse proxy

Unchanged from 2.5: **one domain, port 443 only**, **"Websockets Support"** on at the proxy (Caddy and Traefik: nothing
to do; nginx: upgrade headers). Since calls now go through the built-in node by default, check once after the update:
Admin › Calls › **"Does the proxy pass WebSocket through?" → Check now** – "Yes ✓" is right. Without WebSocket,
`/api/sfu/ws` answers 426; details and troubleshooting in [UPGRADE-2.5.md, section 3](UPGRADE-2.5.md#3-reverse-proxy).

## 5. Federation

**Large calls across servers** need **all involved servers on 2.6** (bugs DB 2439: otherwise, from about 12 devices on,
not every device gets the key). After the update, click **"Sync capabilities now"** (Admin › Calls or Federation) so
connected servers learn the new state right away. Everything else keeps working with servers on 2.5 as before.

## 6. Nodes

- **`HORN_MAX_LEGS`:** the default is **200** from 2.6 (up to 2.5: 48), `HORN_MAX_PER_IP` **40** (up to 2.5: 12). If a
  node still has the old value 48 – set explicitly in the `.env` or the node's configuration, or because a paired node
  still runs 2.5 – Admin › Calls warns at that node: "HORN_MAX_LEGS (48) is too low …" with the value needed (112 with
  the limits above). Fix: remove the entry (then 200 applies) or update the node.
- **Paired nodes** update themselves as soon as no call is running. Only nodes from 2.6 report their cores with the
  container's CPU quota; older ones report the machine's cores – in a container with a CPU quota the estimated capacity
  is then too high.

## 7. Back to 2.5

Only via the backup from before the update (`…-vor-schema-40.dump`), **restored with 2.6**, and only then change
`SAGA_VERSION` – just like [UPGRADE-2.5.md, section 6](UPGRADE-2.5.md#6-troubleshooting-and-way-back):

```sh
docker compose stop saga
docker compose run --rm saga ls /data/backups        # e.g. saga-20261201-183000-vor-schema-40.dump
docker compose run --rm saga python run.py restore /data/backups/saga-20261201-183000-vor-schema-40.dump
# then set SAGA_VERSION=2.5 (or 2.5.0) in .env and:
docker compose pull && docker compose up -d
```

Everything changed after the backup is gone – so only shortly after a failed update.
