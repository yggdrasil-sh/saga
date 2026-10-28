<p align="center">
  <img src="static/icon.svg" width="96" height="96" alt="Saga">
</p>

<h1 align="center">Saga</h1>

<p align="center">
  <b>Your self-hosted home for services, notes and tasks.</b><br>
  Dashboard · Notion-style pages &amp; databases · Tasks · Calendar · Bookmarks · Multi-user
</p>

<p align="center">
  <b>English</b> · <a href="README.de.md">Deutsch</a> · <a href="README.es.md">Español</a>
</p>

<p align="center">
  <a href="https://github.com/yggdrasil-sh/saga/releases"><img src="https://img.shields.io/github/v/release/yggdrasil-sh/saga?label=version" alt="Version"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-blue" alt="License AGPL-3.0"></a>
</p>

<p align="center">
  <a href="https://yggdrasil-sh.github.io/saga/en/">Website</a> ·
  <a href="docs/en/GUIDE.md">Guide</a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#features">Features</a> ·
  <a href="#screenshots">Screenshots</a>
</p>

![Saga dashboard](docs/screenshots/dashboard.png)

Saga is a self-hosted start page and personal knowledge base for families, teams and homelabs: a dashboard for
all your services, Notion-style pages and databases, tasks, calendars and bookmarks. Everyone gets their own
private space – no cloud, no subscription, on your own server.

> **New in Saga 2.4:** **videos in chats** with poster, streaming and resumable upload (up to 500 MB by default, end-to-end
> encrypted), **group calls** (up to 4 devices with video or 6 audio only), **screen sharing with the camera alongside**,
> **picture-in-picture** and **handing a call over to another device** or adding one, **read receipts and online
> status** (can be turned off) and the new media path **saga-media**: chat attachments no longer pass through Python
> (20 × 10 MB in parallel in 0.22 instead of 0.67 s; uploading a 1 GB video costs Python 0.06 % of a core). Plus more
> protection: failed sign-ins slow down instead of locking accounts, login names stay private, Gravatar only with consent.
> Since 2.3: much faster with large databases, federation with chats and calls across servers, encrypted backups,
> Nextcloud files in pages. Details in the [changelog](CHANGELOG.md) (in German).

## Features

- **Dashboard** with tiles for all your services, status dots, favorites, search (optionally web search via
  SearXNG) and keyboard shortcuts
- **Services by group:** members of the group `s_nextcloud` automatically see the "Nextcloud" tile – whether the
  group is local or comes from LDAP/SSO
- **Personal bookmarks** for every user, visible only to them
- **Notion-style pages:** blocks, subpages, templates, versions, trash, Markdown export
- **Databases** with table, board, list, gallery and calendar views, filters, relations and formulas
- **Publish pages:** share a page (optionally with subpages) at `/@name/page` – readable without an account, no
  JavaScript, optional public home page for visitors
- **Sharing and comments:** share pages and databases with people or groups (local, LDAP, SSO) – roles View,
  Comment, Edit, subpages inherit the share; unobtrusive comments on blocks with “Resolved”
- **End-to-end encrypted chats:** direct, group and page chats with attachments, videos (streaming, resumable
  upload), read receipts, online status, replies and @ page links; the
  server (and the admin) only sees encrypted data – libsodium, recovery key, safety numbers, multiple devices;
  archive or delete chats (just for me or for everyone)
- **Audio and video calls:** in direct chats and as group calls, with screen sharing, picture-in-picture and handover
  to another of your devices – WebRTC between the devices, end-to-end encrypted
  and verified with the chat keys, no extra port (optional TURN over 443)
- **Notifications and push:** bell with live updates; optionally to phone/desktop via Web Push (end-to-end encrypted,
  also iPhone as a home screen app), ntfy/UnifiedPush or Apple Push – without content
- **AI assistant (optional):** ask your pages, summarize, suggest tasks, improve or translate text – with a model
  you choose (e.g. Ollama on your own network). Only content you may see, sources linked, suggestions only, off by default.
  With its own name, picture and tone of voice; optionally **in chats on request only** (@name, everything else stays
  encrypted) and with **web search** through your own SearXNG instance (sources linked) – both off by default
- **Federation:** connect Saga servers – share pages with people and groups there (including databases, subpages,
  uploads, versions, export and live presence), find people by name in the shared address book (opt-out per account),
  end-to-end encrypted direct chats, group and page chats and calls across servers, with message requests and blocking.
  Content stays with its owner, every request between servers is signed (Ed25519, HTTP Message Signatures), and a
  misbehaving server is contained (checks, rate limits per server, audit log)
- **Encrypted backups:** daily backups of database, files and keys to **S3** (MinIO, Garage, Backblaze B2, Hetzner,
  AWS …), a **folder** (NAS mount) or **Nextcloud/WebDAV** – encrypted with [age](https://age-encryption.org) and your
  public key (Saga never sees the private key), retention 7/4/6, verification, alerts, test restore, and restore even
  without Saga
- **Personal backups:** everyone backs up their own workspace to their own Nextcloud or WebDAV with their own key – an
  open format (encrypted ZIP with Markdown and JSON), restorable into their own account
- **Nextcloud files in pages:** link your own Nextcloud and insert images, files and folders as blocks – everyone who
  can see the page sees the file, without a Nextcloud account; or take it over as a copy
- **Fast with lots of data:** compressed responses, page tree sync with changes only, long tables render only the
  visible rows, server-side search with a trigram index – measured with a repeatable load test (`tools/perf`)
- **Tasks** with due dates, priorities and recurrence
- **Calendars (CalDAV):** view and edit Nextcloud, mailcow/SOGo, Radicale, iCloud …
- **Immich:** use your own photos as images and cover images
- **Everyone manages their own space:** calendars, Immich, bookmarks, app keys – private, even admins can't
  see the content
- **Accounts:** local users, registration on/off, **LDAP/Active Directory** and **SSO/OIDC**
  (Keycloak, Authentik, Authelia, Zitadel, Entra ID …)
- **Passkeys and two-factor sign-in:** passkeys (also without a password), authenticator app (TOTP) and
  recovery codes; admins can require 2FA for admins or everyone
- **Profile pictures:** upload your own (cropped, re-encoded without metadata) or use Gravatar – fetched and
  cached by the server, browsers never contact Gravatar; optionally from SSO/LDAP
- **Admin area on a separate port** for users, groups, services and sign-in
- **English, German, Spanish, Russian and Thai:** every user can choose their language in their settings,
  and on the sign-in page even before logging in
  (Russian and Thai are machine-translated – improvements from native speakers are welcome)
- **[Apps](https://github.com/yggdrasil-sh/saga-apps)** (iOS/iPadOS/macOS, Linux/GTK, Android) using the same
  API, with persistent sign-in and pairing via QR code

## Quick start

You need a server with Docker and Docker Compose.

```sh
git clone https://github.com/yggdrasil-sh/saga.git
cd saga
cp .env.example .env
nano .env        # set BASE_URL, SECRET_KEY, POSTGRES_PASSWORD (see the comments)
docker compose pull && docker compose up -d
```

**Update:** `git pull && docker compose pull && docker compose up -d` – Saga automatically creates a backup
before any database change. From 1.x to 2.x: set `SAGA_VERSION=2` in `.env` first. 2.4 upgrades the database (schema 14; no way back to 2.3
except via the automatic backup) and turns Gravatar off for all accounts – what to watch out for when updating from 2.3
is in the guide, section 11 “From 2.3 to 2.4”. What's new is listed in the **[changelog](CHANGELOG.md)** (in German) and in the
[releases](https://github.com/yggdrasil-sh/saga/releases). Curious about the latest? `SAGA_VERSION=testing` in
`.env` pulls the testing version from the `testing` branch. Images and files of pages are not part of the
database backup: back up the `saga-data` volume, or run `docker compose exec saga python run.py backup --dateien`.

- **Saga:** `http://<server>:8080` – put it behind a reverse proxy with HTTPS for production use (with a request
  body limit of about 12 MB, see the guide)
- **Admin area:** `http://127.0.0.1:8081` on the server (only reachable locally by default, e.g. via an SSH
  tunnel: `ssh -L 8081:127.0.0.1:8081 server`)
- **First admin account:** `admin` – password from `ADMIN_PASSWORD`, otherwise a generated one is printed in
  `docker compose logs saga`. Forgot it? `docker compose exec saga python run.py admin-passwort`

Everything else – reverse proxy, users, services, LDAP, SSO, calendars, backups, updates – is covered in the
**[guide](docs/en/GUIDE.md)**.

## Screenshots

| | |
|---|---|
| ![Page](docs/screenshots/seite.png) | ![Database as a board](docs/screenshots/board.png) |
| **Pages** with blocks, checklists, tables | **Databases** as table, board, calendar … |
| ![Admin: services](docs/screenshots/admin-dienste.png) | ![Admin: SSO](docs/screenshots/admin-sso.png) |
| **Admin:** services with access via groups | **Admin:** SSO/OIDC with connection test |
| ![Light theme](docs/screenshots/dashboard-hell.png) | ![Settings](docs/screenshots/einstellungen.png) |
| Light theme (follows your system) | Personal settings for every user |

## Architecture

```
Browser / apps ──► saga-media (Go)        :8080  serves chat attachments and videos straight from disk
                        │
                        ▼
                   Saga (Python, FastAPI)         dashboard, pages, API (inside the container on 127.0.0.1:8079)
                   Admin area              :8081  separate sign-in, admins only
                        │
                        ├── PostgreSQL     accounts, pages, tasks, settings
                        ├── /data          images and files from pages
                        └── optional: LDAP/AD, OIDC provider, SearXNG, CalDAV, Immich
```

## Security at a glance

- Passwords hashed with scrypt, failed attempts only slow down the attacking address (nobody can lock you out of your own account),
  CSRF protection, strict Content Security Policy
- Login names stay private: others see the display name, public name or a handle
- Calendar, Immich, LDAP and SSO credentials encrypted in the database (key: `SECRET_KEY`)
- Backups encrypted with public keys (age) – the server never holds the private key
- User data strictly separated – tested automatically (`tests/isolation.py`)
- Internal (home network) addresses for calendars/Immich only after the admin allows them (SSRF protection)
- Locking an account immediately ends all its sessions and app sign-ins

Found a security issue? Please don't open a public issue – report it via
[Security Advisories](https://github.com/yggdrasil-sh/saga/security/advisories/new).

## Development &amp; tests

The tests run against a real stack with PostgreSQL, Radicale (CalDAV), a test OpenLDAP and a test OIDC
provider:

```sh
SAGA_SSH=root@dockerhost tools/test-stack.sh
```

Screenshots for this page: `SAGA_SSH=… SAGA_SCRIPT=screens-remote.sh tools/test-stack.sh`.

## License

[GNU AGPL-3.0](LICENSE) – you may use, modify and share Saga freely; if you run a modified version as a
service for others, you must publish its source code as well. Bundled Editor.js components are under their own
licenses (`static/vendor/editorjs/licenses`). Math rendering uses [KaTeX](https://katex.org) (MIT,
`static/vendor/katex/LICENSE`), diagrams use [Mermaid](https://mermaid.js.org) (MIT, `static/vendor/mermaid/LICENSE`,
bundled DOMPurify under Apache-2.0/MPL-2.0), and published pages render math via
[latex2mathml](https://pypi.org/project/latex2mathml/) (MIT). Simultaneous editing (live) uses
[pycrdt](https://pypi.org/project/pycrdt/) (MIT, Yjs-compatible). Service icons from [selfh.st/icons](https://selfh.st/icons).
