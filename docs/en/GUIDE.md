# Saga – Guide

**English** · [Deutsch](../ANLEITUNG.md) · [Español](../es/GUIA.md)

This guide takes you from installation to everyday use: first for the person who runs Saga (the admin),
then for everyone who uses it.

**Contents**

1. [Requirements](#1-requirements)
2. [Installation](#2-installation)
3. [HTTPS with a reverse proxy](#3-https-with-a-reverse-proxy)
4. [The admin area](#4-the-admin-area)
5. [Users and registration](#5-users-and-registration)
6. [Groups and services](#6-groups-and-services)
7. [LDAP / Active Directory](#7-ldap--active-directory)
8. [SSO with OpenID Connect](#8-sso-with-openid-connect)
9. [Using Saga](#9-using-saga)
10. [Web search (optional)](#10-web-search-optional)
11. [Backup, restore, updates](#11-backup-restore-updates)
12. [Troubleshooting](#12-troubleshooting)
13. [All .env settings](#13-all-env-settings)

---

## 1. Requirements

- A Linux server (or VM/LXC) with **Docker** and **Docker Compose** (`docker compose version`)
- About 512 MB of RAM and a few GB of disk space (depending on how many images your pages contain)
- For access from outside your network: a domain name and a reverse proxy with HTTPS (see section 3)

Saga does **not** need an external sign-in service: local accounts work right away. You can add LDAP and SSO
later at any time in the admin area.

## 2. Installation

```sh
git clone https://github.com/yggdrasil-sh/saga.git
cd saga
cp .env.example .env
```

Open `.env` and set at least these three values:

| Setting | Meaning | Example |
|---|---|---|
| `BASE_URL` | The address people use to open Saga | `https://saga.example.org` |
| `SECRET_KEY` | Secret key for sessions and encrypted credentials | `openssl rand -hex 32` |
| `POSTGRES_PASSWORD` | Database password (internal only) | `openssl rand -hex 24` |

> ⚠️ **Keep `SECRET_KEY` safe and never change it.** Stored calendar, Immich, LDAP and SSO credentials are
> encrypted with it. A new key makes them unreadable (users then have to enter them again).

Optional: `ADMIN_USERNAME` and `ADMIN_PASSWORD` (at least 10 characters) for the first admin account.

Start Saga:

```sh
docker compose pull        # ready-made image from GitHub (ghcr.io/yggdrasil-sh/saga)
docker compose up -d
docker compose ps          # db and saga should be "healthy"
```

Prefer to build it yourself (for example with your own changes)? `docker compose up -d --build` builds the
image from source.

Saga is now available at `http://<server>:8080`.

**First admin account:** If you did not set `ADMIN_PASSWORD`, Saga generates one on first start and writes it
to the log and to the file `/data/admin-passwort.txt` inside the container:

```sh
docker compose logs saga | grep Admin-Konto
```

Forgot the password or locked yourself out? This command sets a new random password, unlocks the account and
gives it admin rights again:

```sh
docker compose exec saga python run.py admin-passwort          # account "admin"
docker compose exec saga python run.py admin-passwort anna     # another account
```

![Sign-in page](../screenshots/login.png)

## 3. HTTPS with a reverse proxy

Saga itself only speaks HTTP. In production, put a reverse proxy with HTTPS in front of it. Important:

- `BASE_URL` in `.env` must be the **https address** people use to open Saga.
- `TRUSTED_PROXIES` = the proxy's IP, so Saga sees the visitors' real addresses (needed for the lockout after
  failed sign-in attempts). If the proxy runs on the same server, it is enough to bind the port locally
  (`HOST_BIND=127.0.0.1`) and set `TRUSTED_PROXIES=*`.
- Requests to other host names are redirected to `BASE_URL` (`CANONICAL_REDIRECT=true`).

**Caddy** (gets the certificate automatically):

```
saga.example.org {
    reverse_proxy 127.0.0.1:8080
}
```

**nginx:**

```nginx
server {
    listen 443 ssl http2;
    server_name saga.example.org;
    # ssl_certificate ... / ssl_certificate_key ...
    client_max_body_size 20m;              # images/files in pages
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## 4. The admin area

The admin area runs on its **own port (8081)** with its own sign-in. By default it is only reachable on the
server itself (`ADMIN_BIND=127.0.0.1`). From your computer, use an SSH tunnel:

```sh
ssh -L 8081:127.0.0.1:8081 user@server
# then open http://localhost:8081 in your browser
```

For access from your home network, you can set `ADMIN_BIND` to the server's LAN IP. **Never expose the admin
port to the internet.**

Every account with admin rights can sign in here – local or LDAP – even if local accounts are disabled on the
regular sign-in page.

![Admin overview](../screenshots/admin-uebersicht.png)

Under **General** you can set:

| Setting | Effect |
|---|---|
| Title | Name shown at the top of the dashboard and on the sign-in page |
| Allow registration | Anyone who can reach the page can create an account |
| Sign-in with local accounts | Off = the sign-in page only offers LDAP/SSO |
| Default groups | Every new account gets these (e.g. `s_wiki` for a service everyone should see) |
| Group prefix | Prefix of the access groups for services, default `s_` (see section 6) |
| Internal addresses | Whether users may connect calendars/Immich on the home network (see section 9) |
| Default language | Language of the sign-in page, the admin area and all accounts set to "Automatic"; default: Automatic (browser) |

![Admin: General](../screenshots/admin-allgemein.png)

## 5. Users and registration

Under **Users** you create accounts, set passwords, and assign groups and admin rights.

- **Local accounts** have a password stored in Saga (at least 10 characters, stored as a scrypt hash).
- **LDAP and SSO accounts** are normally created automatically on first sign-in. If "create automatically" is
  off, create them here in advance with the account type LDAP or SSO.
- **Lock** immediately ends all sessions, app sign-ins and app keys of the account.
- **Sign out everywhere** helps when a device has been lost.
- **Delete** removes the account **including all its data** (pages, tasks, files, bookmarks, connections).
  Better to lock external accounts: otherwise a new, empty account would be created on the next sign-in.

Users' content is private – even admins only see counts, not the pages themselves.

![Admin: Edit user](../screenshots/admin-benutzer-detail.png)

**Self-registration:** Turn on "Allow registration" under General. The sign-in page then shows a link to
register. New accounts get the default groups. While LDAP is active, nobody can register a name that already
exists in the directory.

## 6. Groups and services

**Services** are the tiles on the dashboard (Nextcloud, Jellyfin, Home Assistant …). Groups control who sees
which service:

> A service with the short name **`nextcloud`** is visible to all members of the group **`s_nextcloud`**
> (group prefix `s_` + short name).

This works the same with **local groups**, groups from **LDAP/AD** or from your **SSO provider** – simply
create a group `s_nextcloud` there. When you add a service, Saga creates the matching local group
automatically.

For each service you can also set:

- **Additional groups**, e.g. `family` or `admins`
- **For all signed-in users**
- **Icon** – the name from [selfh.st/icons](https://selfh.st/icons), e.g. `jellyfin`, `home-assistant`
- **Category** and **order**
- **Check availability** – Saga requests the address every minute and shows a status dot

![Admin: Services](../screenshots/admin-dienste.png)

**Services from LDAP (FreeIPA style):** Under LDAP you can turn on "Take services from group descriptions".
Every group with the prefix whose description looks like this then becomes a service:

```
Any text || Nextcloud|https://cloud.example.org|nextcloud|Files
```

Several services per group: append more `|| Name|URL|Icon|Category` entries.

## 7. LDAP / Active Directory

Users sign in on the regular sign-in page with their directory password. Saga reads their groups again on every
sign-in (and whenever an app renews its session).

1. Open **LDAP / AD**, choose a **template** (FreeIPA, Active Directory, OpenLDAP) and apply it.
2. Enter the **server**: `ldaps://ipa.example.org`, or `ldap://dc1.example.org` with StartTLS.
3. Enter a **service account** (read-only) and its password.
4. Adjust the **search base** for users and groups.
5. Enter a user name at the bottom and click **Test connection** – Saga shows the DN, name, email and groups.
6. **Enable LDAP** and save.

| | FreeIPA | Active Directory | OpenLDAP |
|---|---|---|---|
| Server | `ldaps://ipa.example.org` | `ldaps://dc1.example.org` | `ldap://ldap.example.org` + StartTLS |
| Service account | `uid=saga,cn=sysaccounts,cn=etc,dc=example,dc=org` | `CN=saga,OU=Services,DC=example,DC=org` | `cn=saga,dc=example,dc=org` |
| User search base | `cn=users,cn=accounts,dc=example,dc=org` | `OU=Users,DC=example,DC=org` | `ou=people,dc=example,dc=org` |
| Groups | memberOf on the user | AD including nested groups | search groups |

More options: only **members of this group** may sign in; members of the **admin group** become admins.
Using your own certificate authority (CA)? Put the file as `.crt` into the `certs/` folder next to
`docker-compose.yml` and run `docker compose restart saga`.

![Admin: LDAP](../screenshots/admin-ldap.png)

## 8. SSO with OpenID Connect

Sign in via Keycloak, Authentik, Authelia, Zitadel, Entra ID, Google and others. At your provider, create a
**confidential client** (authorization code, PKCE):

- **Redirect URI:** `https://saga.example.org/auth/callback`
- **Post-logout redirect URI:** `https://saga.example.org/login?bye=1`

In Saga, enter under **SSO / OIDC**:

| Field | Example (Keycloak) |
|---|---|
| Issuer URL | `https://sso.example.org/realms/home` |
| Client ID / client secret | from the client at your provider |
| Username claim | `preferred_username` |
| Groups claim | `groups` (in Keycloak, add a "Group Membership" mapper with "Full group path" off) |

**Test connection** checks the provider's discovery document. Then **enable SSO** and save – the sign-in page
shows the SSO button. If local accounts are disabled and LDAP is not active, the sign-in page goes straight to
the provider.

Groups such as `s_nextcloud` from the SSO provider unlock services just like local groups.

**SSO provider backed by a directory** (e.g. Keycloak connected to FreeIPA or AD): instead of setting up a
groups claim, Saga can read the groups directly from the directory. Enter the directory under **LDAP / AD** and
turn on **"Read groups of SSO accounts from the directory"**. If the sign-in page should only offer SSO, turn
off "Sign-in with directory password". Accounts locked in the directory then lose access to Saga immediately as
well (with FreeIPA via the template). **Members of this group** and **admin group** work as with LDAP.

> **Security:** An SSO or LDAP sign-in never takes over an existing account of a different origin with the
> same name. If registration is open, someone could claim a name in advance – with SSO, keep registration off
> or create the accounts in advance.

![Admin: SSO](../screenshots/admin-sso.png)

## 9. Using Saga

### Dashboard

Tiles for your services and bookmarks, sorted by category. **★** makes a tile a favorite. On the right you see
your open tasks and today's and tomorrow's events.

| Key | Action |
|---|---|
| `/` | Search |
| `1`–`9` | Open tile |
| `Enter` | First result (otherwise web search) |
| `t` | New task |
| `n` | Notes |

![Dashboard](../screenshots/dashboard.png)

### Bookmarks

**+ Bookmark** at the bottom of the dashboard, or in the user menu under Settings. Only you can see your
bookmarks – in the apps too. Icon: a name from [selfh.st/icons](https://selfh.st/icons), otherwise the first
letter is shown. Edit via **✎** on the tile.

![Bookmarks](../screenshots/lesezeichen.png)

### Pages and databases

Your page tree lives under **Notes**. Type `/` for new blocks: headings, lists, checklists, tables, code,
images, files, columns, links to other pages, events and more. Pages have versions, a trash (30 days) and can
be exported as Markdown.

![Page](../screenshots/seite.png)

**Databases** collect entries with properties (status, date, number, select, relation, formula …) and show them
as a table, board, list, gallery or calendar – with filters, sorting and totals.

![Database as a board](../screenshots/board.png)

### Account, calendars, Immich, apps

In the user menu at the top right under **Settings**:

- **Account:** change your name, email and password (for LDAP/SSO accounts, at your provider)
- **Calendars:** connect CalDAV accounts (Nextcloud: `https://cloud.example.org/remote.php/dav/`).
  Ideally create a separate app password just for calendars.
- **Immich:** enter the address and an API key with the permissions `album.read`, `asset.read`, `asset.view`,
  `asset.download`, `user.read`. Your photos are then available as images and cover images.
- **Apps & devices:** a list of all signed-in apps. Each one comes with a small key for the widget, share
  extension and shortcuts; **Sign out** removes both. Keys created by hand (e.g. for scripts) are listed,
  collapsed, under "More keys".
- **Sign in with QR code:** "Apps & devices" shows a QR code that is valid for two minutes and can be used only
  once. Scan it with your phone's camera – the app takes over the server address and sign-in, without typing a
  password on the device. The link in the QR code also works as a pairing link in desktop apps.

Public calendar and Immich servers (https) always work. **Servers on your home network** (e.g.
`192.168.1.20` or `nextcloud.lan`) must first be allowed by the admin under General – this keeps the internal
network from being queried through Saga.

![Settings](../screenshots/einstellungen.png)

### Language

Saga speaks English, German and Spanish. The language you see is decided in this order:

1. your own choice under **Settings** → **Account** → **Language** – it applies immediately and in every
   browser, because it is stored in your account,
2. the **default language** the admin sets under General,
3. your browser's language,
4. otherwise English (also for any other browser language).

"Automatic" always means: the next step decides. Dates and numbers follow the selected language. The apps follow
your device's language.

### On your phone

Saga adapts to small screens and can be added to your home screen like an app ("Add to Home Screen" in the
browser menu).

<img src="../screenshots/handy.png" width="320" alt="Saga on a phone">

## 10. Web search (optional)

The search field can also search the web – through your own SearXNG instance that only Saga can reach:

```sh
# in .env
COMPOSE_PROFILES=search
SEARXNG_URL=http://searxng:8080
SEARXNG_SECRET=<openssl rand -hex 32>
```

Then run `docker compose up -d`. Search queries only go to your server, not directly to search engines.

## 11. Backup, restore, updates

**What you need to back up:**

1. the database,
2. the volume `saga-data` (images and files from pages),
3. the `.env` file (above all `SECRET_KEY`).

```sh
# back up the database
docker compose exec -T db pg_dump -U saga -d saga -Fc > saga-$(date +%F).dump
# back up the files
docker run --rm -v saga_saga-data:/data -v "$PWD":/backup alpine tar czf /backup/saga-data-$(date +%F).tgz -C /data .
```

```sh
# restore (empty installation, same SECRET_KEY)
docker compose up -d db
docker compose exec -T db pg_restore -U saga -d saga --clean --if-exists < saga-2026-10-13.dump
docker run --rm -v saga_saga-data:/data -v "$PWD":/backup alpine tar xzf /backup/saga-data-2026-10-13.tgz -C /data
docker compose up -d
```

(The volume name starts with the name of the project folder – `docker volume ls` shows it.)

### Updates

Saga uses version numbers according to [Semantic Versioning](https://semver.org/): **1.2.3** =
major.minor.patch. What has changed is listed in the [changelog](../../CHANGELOG.md) (in German) and in the
[releases](https://github.com/yggdrasil-sh/saga/releases). You can see the installed version at the bottom of
the admin area; a notice also appears there when a new version is available (can be turned off under General).

`SAGA_VERSION` in `.env` decides which version runs:

| Value | Meaning |
|---|---|
| `1` (default) | always the latest 1.x – new features and fixes, nothing that needs manual work |
| `1.2` | only bug fixes for 1.2 |
| `1.2.3` | exactly this version |
| `testing` | **Testing version:** the latest from the `testing` branch, before the release. May contain bugs – only for trying things out |

**Updating:**

```sh
git pull                                  # docker-compose.yml and guide up to date
docker compose pull                       # new image
docker compose up -d                      # restart
docker compose logs saga | grep saga:     # backup and database migration in the log
```

If an update changes the database, Saga **automatically creates a backup first** in `/data/backups`
(in the `saga-data` volume; the last 10 are kept, see `BACKUP_KEEP` in `.env`). To create a backup by hand:

```sh
docker compose exec saga python run.py backup
docker compose exec saga ls -l /data/backups
```

**Testing version:** With `SAGA_VERSION=testing` you get new features as soon as they are ready – before the
release. The version is then called something like `1.0.0-testing.ab12cd3` (shown with 🧪 at the bottom of the
admin area). Back to the stable version: `SAGA_VERSION=1`, then `docker compose pull && docker compose up -d`.
If the testing version has already changed the database, the stable version only starts once it contains that
change too (with the next release at the latest) – or you restore the backup from before the switch.

A new **major version** (2.0.0) may need manual work – the changelog will say so. To get it, set
`SAGA_VERSION=2`.

**Going back to the previous version:** An older Saga will not start on a database that a newer version has
already changed (it reports this in the log instead of damaging data). In that case, restore the backup from
before the update:

```sh
docker compose stop saga
docker compose run --rm saga ls /data/backups                     # e.g. saga-20261013-183000-vor-schema-3.dump
docker compose run --rm saga python run.py restore /data/backups/saga-20261013-183000-vor-schema-3.dump
# set SAGA_VERSION in .env to the old version (e.g. 1.2.3), then:
docker compose pull && docker compose up -d
```

Everything changed after the backup is lost – so only do this shortly after a failed update.

**Apps:** The apps check the server version on start. If an app is too old for the server, it asks you to
update it. The app's settings show both the app and the server version.

## 12. Troubleshooting

**After signing in I end up on the sign-in page again.**
`BASE_URL` must be exactly the address shown in the browser (including `https://`). With `https`, Saga only
sets cookies over HTTPS.

**"Too many failed attempts".**
After 8 wrong passwords per account (30 per address) within 15 minutes, Saga makes you wait. Behind a reverse
proxy, set `TRUSTED_PROXIES` – otherwise all visitors share the proxy's address.

**Service icons are missing.**
Saga downloads them once from `cdn.jsdelivr.net` (selfh.st) and stores them. Without internet access, the first
letter is shown. With `ICON_CDN=` (empty), Saga fetches nothing from the internet at all.

**LDAP: service account sign-in failed, or certificate errors.**
Check with "Test connection". With your own CA, put the file into `certs/` (section 7).

**SSO: SSO sign-in failed.**
Check the redirect URI at your provider (`BASE_URL/auth/callback`) and verify the discovery document with
"Test connection". Details are in `docker compose logs saga`.

**Calendar: server not reachable or not allowed.**
Internal servers must be allowed under Admin → General.

## 13. All .env settings

| Variable | Default | Meaning |
|---|---|---|
| `BASE_URL` | – (required) | Public address of Saga |
| `SECRET_KEY` | – (required) | At least 32 characters, never change it |
| `POSTGRES_PASSWORD` | – (required) | Database password |
| `POSTGRES_USER` / `POSTGRES_DB` | `saga` | Database user and name |
| `ADMIN_URL` | – | "Admin area" link in the user menu of admins |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / generated | First admin account |
| `HOST_BIND` / `HOST_PORT` | `0.0.0.0` / `8080` | Where Saga listens on the server |
| `ADMIN_BIND` / `ADMIN_HOST_PORT` | `127.0.0.1` / `8081` | Where the admin area listens |
| `TRUSTED_PROXIES` | `127.0.0.1` | IPs of the reverse proxy (`*` = all) |
| `CANONICAL_REDIRECT` | `true` | Redirect other host names to `BASE_URL` |
| `SESSION_HOURS` | `12` | Lifetime of a browser session (apps stay signed in) |
| `SAGA_VERSION` | `1` | Which version `docker compose pull` fetches: `1`, `1.2` or `1.2.3` (section 11) |
| `BACKUP_KEEP` | `10` | How many automatic/manual backups are kept in `/data/backups` |
| `SAGA_UPDATE_CHECK` | `1` | `0` = never ask GitHub for new versions (can also be turned off in the admin area) |
| `ADMIN_SESSION_HOURS` | `4` | Lifetime of a session in the admin area |
| `TIMEZONE` | `Europe/Berlin` | Time zone for exports |
| `ATTACH_MAX_MB` / `ATTACH_QUOTA_MB` | `10` / `500` | Size per file / storage per user |
| `NOTE_MAX_COUNT` | `5000` | Pages per user |
| `IMMICH_URL` | – | Suggestion in the Immich dialog (allowed automatically) |
| `ICON_CDN` | selfh.st via jsDelivr | Source of service icons; empty = none |
| `STATUS_INTERVAL` | `60` | Seconds between status checks |
| `CACHE_TTL` | `60` | Seconds that account and service data is cached |
| `SEARXNG_URL` / `SEARXNG_SECRET` | – | Web search (section 10) |
| `SEARCH_LANGUAGE` / `SEARCH_SAFESEARCH` | `de-DE` / `0` | Language and filter of the web search |
| `LOG_LEVEL` | `info` | Log verbosity |
| `DATABASE_URL` | from `POSTGRES_*` | Custom database connection (instead of the `db` container) |
