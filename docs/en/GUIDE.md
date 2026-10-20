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

If the account has a second factor (passkey, authenticator app), this emergency command removes it as well –
afterwards you sign in with the new password only and set up the second factor again. If the "Require 2FA" policy
applies, the next sign-in (in the admin area too) leads straight to the setup – you are not locked out. More
emergency commands:

```sh
docker compose exec saga python run.py 2fa-zuruecksetzen anna   # remove only the second factor (password stays, LDAP too)
docker compose exec saga python run.py 2fa-richtlinie           # show the "Require 2FA" policy
docker compose exec saga python run.py 2fa-richtlinie none      # … turn it off (none | admins | all)
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
    request_body {
        max_size 12MB                      # largest request: file upload (ATTACH_MAX_MB + 2)
    }
    reverse_proxy 127.0.0.1:8080
}
```

**nginx:**

```nginx
server {
    listen 443 ssl http2;
    server_name saga.example.org;
    # ssl_certificate ... / ssl_certificate_key ...
    client_max_body_size 12m;              # largest request: file upload (ATTACH_MAX_MB + 2)
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

**Live channel (notifications, presence, live updates):** Saga sends updates over a connection that stays open
(server-sent events, `GET /api/events`) – on the same port as everything else, with no WebSocket and no extra port. The
proxy must not buffer this response and must not close the connection after a short time. Saga helps on its own: it
sends `X-Accel-Buffering: no` and `Cache-Control: no-cache`, plus a heartbeat line every 25 seconds so idle timeouts
don't kick in.

- **Caddy:** nothing to do – `reverse_proxy` passes `text/event-stream` through immediately.
- **nginx:** already works with the configuration above (tested: no buffering, the connection stays open beyond the
  default `proxy_read_timeout` of 60 s). Recommended – and required if another buffering proxy sits in front – is a
  separate block inside `server { … }`:

```nginx
    location /api/events {                 # live channel: no buffering, keep open
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 1h;
    }
```

- **Traefik:** works without any settings. Don't put a `buffering` middleware on the router and don't set idle
  timeouts below 30 seconds.
- **NetBird reverse proxy and others:** tested are Caddy 2, nginx 1.27 and Traefik 3.1. To check your proxy: sign in
  with two accounts in two browsers and share a page from one with the other – the bell in the other browser must show
  a 1 right away. In the developer tools (network), `/api/events` then stays open as “eventsource”. If a proxy buffers
  or drops the connection, Saga switches to polling every 60 seconds on its own: everything still arrives, just later.

**Request size:** Saga itself rejects requests that are too large with 413 – in general above 2 MB
(`REQUEST_MAX_MB`), file uploads above `ATTACH_MAX_MB` (plus a little form overhead), the admin area above 1 MB.
A limit in the reverse proxy (as above, slightly above `ATTACH_MAX_MB`) is still recommended: huge requests then
never reach Saga. If you raise `ATTACH_MAX_MB`, raise the proxy limit too.

### Media path (saga-media)

Since 2.4 the container includes a small extra service, **saga-media** (Go, standard library only, source in `media/`).
It listens on the usual port 8080 and forwards everything to Saga – with one exception: it serves chat attachments
(`GET /api/chat/files/…`) itself, straight from disk, with range requests, `sendfile` and timeouts. File bytes no longer
pass through Python, so many parallel downloads don't slow the server down. Saga still decides who may see what:
saga-media asks once per file and session and remembers the answer for at most 60 seconds. Removing someone from a chat
or deleting a message therefore takes effect within a minute at the latest (a deleted file immediately).

- **Nothing to set up:** no new port, no change to the reverse proxy, `TRUSTED_PROXIES` works as before. Inside the
  container Saga itself now only listens on `127.0.0.1:8079` (`MEDIA_INTERNAL_PORT`).
- **Turn it off:** `SAGA_MEDIA=off` in `.env` – Saga then runs directly on port 8080 as up to 2.3.
- The admin area shows under Overview → “Media” how chat attachments are served.

**Without a container** (Saga started with `python run.py`): either build saga-media and start it in front
(`cd media && CGO_ENABLED=0 go build -o saga-media .`, then `saga-media -- python run.py` with the same environment), or
let the reverse proxy serve the files (`MEDIA_ACCEL`). After checking permissions Saga then only answers with an
`X-Accel-Redirect` header and the proxy reads the file from disk. The proxy needs read access to `STATE_DIR/chat`
(`/var/lib/saga/chat` in the examples).

`MEDIA_ACCEL=nginx` – add inside `server { … }`:

```nginx
    location /_saga_media/ {               # chat attachments: internal only, straight from disk
        internal;
        alias /var/lib/saga/chat/;         # STATE_DIR/chat
        types { }
        default_type application/octet-stream;
        add_header X-Content-Type-Options nosniff always;
        add_header Content-Security-Policy "sandbox; default-src 'none'" always;
    }
```

`MEDIA_ACCEL=caddy` – in the `reverse_proxy` block:

```
saga.example.org {
    reverse_proxy 127.0.0.1:8080 {
        @accel header X-Accel-Redirect *
        handle_response @accel {
            root * /var/lib/saga/chat         # STATE_DIR/chat
            rewrite * {rp.header.X-Accel-Redirect}
            copy_response_headers {
                include Cache-Control Content-Disposition
            }
            header Content-Type application/octet-stream
            header X-Content-Type-Options nosniff
            header Content-Security-Policy "sandbox; default-src 'none'"
            file_server
        }
    }
}
```

Both examples are tested with nginx 1.24 and Caddy 2.8 (`tests/medienpfad.py`). Without saga-media and without
`MEDIA_ACCEL`, Saga serves the files itself as before, but at most `MEDIA_PY_STREAMS` (default 4) at a time; further
downloads wait briefly. The admin area points this out.

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
- **Reset 2FA** (card "Second factor"): removes an account's passkeys, authenticator app and recovery codes – only
  if the person has lost everything and you are sure it is them. Who reset it and when is shown in the log and on
  the user page.
- **Require 2FA** (General → "Second factor"): nobody (default), admins only, or all local and LDAP accounts.
  Anyone affected without a second factor is taken to the setup after signing in – nobody is locked out, and
  signed-in apps and sessions stay signed in. This also applies to the **admin area** itself: an admin without a
  factor sets up an authenticator app after the password (QR code, then code) and is then signed in; admin sessions
  that are already open stay. With "all", **new app sign-ins** (the app's sign-in window, QR pairing) need a factor
  too. Details in section 9 "Passkeys and two-factor sign-in", emergency commands in section 2.
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

**Fixed SSO identifier:** On the first SSO sign-in, Saga remembers the provider's identifier (`sub`). If someone
later signs in with the same username but a different identifier, they are rejected ("linked to a different SSO
identity"). **Switched providers** (all identifiers are new)? In the admin area, open Users → the account →
**"Remove SSO link"** – the next SSO sign-in with this username links the account again, all data is kept. Only
do this if you're sure the same person has this username at the provider.

![Admin: SSO](../screenshots/admin-sso.png)

## 9. Using Saga

### Dashboard

Tiles for your services and bookmarks, sorted by category. **★** makes a tile a favorite. On the right you see
your open tasks and today's and tomorrow's events. Search finds services, bookmarks and – below them – your
**pages** too (title and content).

| Key | Action |
|---|---|
| `/` | Search |
| `1`–`9` | Open tile |
| `Enter` | First result (otherwise web search) |
| `t` | New task |
| `n` | Pages |

![Dashboard](../screenshots/dashboard.png)

### Bookmarks

**+ Bookmark** at the bottom of the dashboard, or in the user menu under Settings. Only you can see your
bookmarks – in the apps too; the admin only sees how many there are. The address is enough: without a name, the
tile is named after the host (e.g. `wiki.example.org`). Icon: a name from [selfh.st/icons](https://selfh.st/icons),
otherwise the first letter is shown. Edit via **✎** on the tile.

![Bookmarks](../screenshots/lesezeichen.png)

### Tasks

- **Add:** type into **Quick add …** in the sidebar on the right and press `Enter` (the `t` key jumps there). If
  the sidebar is collapsed (**»**) or the window is narrow, your tasks are shown in the **Tasks** box in the main
  area instead – never both at once. There you can set **priority**, **repeat** (daily, weekdays …) and a **due
  date** right away (icons in the input field). **All tasks …** at the bottom of the sidebar opens this box.
- **Date:** via the calendar icon when adding, or later via **⋯** on the task → **Choose date …**. Tasks due
  today show “today”, overdue ones are red.
- **Complete:** click the circle in front of the task. **“Task completed · Undo”** appears at the bottom; for
  about 8 seconds **Undo** brings it back. For repeating tasks it shows the next date, and Undo also removes the
  next task that was already created.
- **Completed:** **Show completed** below the list. Clicking the check mark reopens a task, **×** deletes it.

The same tasks are available in the apps and – as the `/Tasks` block – in pages.

### Pages and databases

Your page tree lives under **Pages**. Type `/` for new blocks: headings, lists, checklists, tables, code,
images, files, columns, links to other pages, events and more. Keep typing to filter the menu; `Enter` picks the
first match, `Esc` closes it and removes the “/” again. `Ctrl`+`K` (Mac: `⌘`+`K`) searches titles and content.
Pages have versions, a trash (30 days) and can be exported as Markdown.
**Copying images:** a click on an image selects it; `Ctrl`+`C`/`X` (Mac: `⌘`) copies or cuts it, `Ctrl`+`V` pastes it
into this or another page (also in another tab) – caption included. On a page of another account, its owner gets their
own copy of the file. Other apps receive the image as PNG where the browser allows it.

![Page](../screenshots/seite.png)

**Databases** collect entries with properties (status, date, number, select, relation, formula …) and show them
as a table, board, list, gallery or calendar – with filters, sorting and totals. In a table, `Enter` in the name
moves to the next row; in the last row it creates the next entry right away.

![Database as a board](../screenshots/board.png)

**Nextcloud files in pages:** under Settings → **Nextcloud** you link your **own** Nextcloud (enter the address, sign in
to Nextcloud in the new tab and allow access – Saga gets its own app password and never sees your password). The “/”
menu then offers **“Nextcloud file”**: recently changed files, browse folders or search by name. A Nextcloud link of
your own instance pasted into a page (`…/f/123`, `…/apps/files/?fileid=123`) turns into a block automatically. Two
ways:

- **Insert as link:** the file stays in Nextcloud. Images appear at page width, other files as a card with a preview
  (if Nextcloud provides one), “Download” and “Open in Nextcloud”; folders show their first entries. **Everyone who
  can see the page also sees the file** – preview and download go through Saga with your account, also for people
  without a Nextcloud of their own. If you disconnect, lose the right to edit the page or the file is gone from
  Nextcloud, the block only shows a card without content.
- **Copy into Saga:** the file becomes a normal Saga file (image or file block) and then follows Saga's permissions –
  also offline, in the backup and in the export.

Whoever may edit a page of another account can only insert files from their **own** Nextcloud; other people's blocks
stay unchanged and can't be copied into other pages. Only the person who inserted a block sees two switches below it
(default off): **“Show on published page”** (visitors see preview and download through the page's address, never the
Nextcloud address) and **“Show via connected servers”** (people on other Saga instances the page is shared with). The
export contains a link instead of the file, and search finds the file name. Without approval in the admin area only
public https addresses work; the admin allows home network servers under General → “User connections”, where the
feature can also be turned off completely. Saga caches thumbnails (`/data/nc-cache`, not in the backup, limit
`NC_CACHE_MB`).

### Publishing pages

Any page can be made **public** – readable without signing in, shown in a clean layout, light or dark depending on
the device and easy to read on a phone. In the page menu (**⋯** at the right above the page, next to “Saved”)
choose **“Publish …”** and turn on the **“Public”** switch in the dialog – from then on the page is reachable at its
address. From top to bottom the dialog shows: the switch, the link (with Copy and Open, on phones also Share), the
address, the options and “Stop publishing”. While the page is private, the future address is shown dimmed and not yet
as a link, and the options are grouped under “Applies once the page is public”. For a public page, changes to the
options apply immediately; only a new address is applied explicitly with “Change address”. Errors (e.g. an address
that is already in use) appear right below the field.

- **Public name:** The first time, you choose a public name (suggested from your display name, 2–32 characters
  `a–z 0–9 - _`). It appears in every address and is visible to everyone – it is **never** your username. Change it
  in Settings → Account in its own “Public name” block (with its own “Save name” button; Saga checks while you type
  whether the name is valid and available, and shows “New address: … (was …)”); old links stop working
  afterwards. Names such as `admin`, `api` or `login`
  are reserved.
- **Address:** `https://saga.example.org/@name/page-address`. The page address is suggested from the title and can
  be changed (2–60 characters): as soon as what you type differs from the saved address, the **“Change address”**
  button appears – after a confirmation, because the current link stops working. Saga redirects uppercase letters or a trailing “/” to the correct address.
- **Also publish subpages:** All subpages (and database entries) below this page become readable too, at
  `/@name/page-address/<number>`. Without this switch they stay private. Such subpages show “🌐 Public (via “parent
  page”)” in the page header; their dialog explains this and links to the parent page.
- **Allow search engines:** Off by default – the page then asks search engines not to index it.
- **As profile page:** This page appears at `/@name` (one per account at most). Without a profile page, `/@name`
  shows a plain list of your public pages. If another page already is your profile page, the dialog names it – the
  new one replaces it. If the page is your profile page, the dialog also shows the **profile address** `…/@name`
  below the link, ready to copy.
- **Copy link / Open** (on phones also **Share**) and **Stop publishing** (with a confirmation). Published pages show “🌐 Public” in the page header – a click opens
  the dialog, the “↗” next to it opens the public page.
- **Stopping remembers everything:** address and settings are kept (the address stays reserved for this page). If
  you publish the page again, it uses the same address as before.

**What becomes public – and what doesn't:** title, icon, cover and content (text, headings, lists, checklists,
quotes, code, images, tables, columns, callouts, toggles). Databases appear as a simple table (title and the visible
properties of the view). Embeds become links. **Not** published: task, calendar and Immich blocks, buttons and file
attachments. Links to other pages only stay links if the target is public as well – otherwise just the text remains.
Saga only serves images that are used in exactly this page.

**How visitors see the page:** Below the title it says “@name · updated Oct 14, 2026” (linked to `/@name`). There is
a top bar only on subpages (path back) and on the home page for visitors (name of the instance, “Sign in”). The bar, the date and messages appear in the visitor's
language; the content is marked with your account's language (important for screen readers and hyphenation). Text
is set at a comfortable reading width, images and tables may be wider. An address that doesn't exist offers a next
step: “Go to the home page” (if the admin picked one) or “Sign in”.

**Trash and archive:** Archived pages and pages in the trash are not reachable (“Not found”). The publication is
kept, though: if you restore the page, it is public again at the same address. It only disappears when the page is
deleted permanently (or the account is deleted).

**For the admin** (admin area → General → “Publish”):

- **Allow publishing** (on by default). Off = all public addresses return “Not found” immediately; the page settings
  are kept.
- **Home page for visitors:** an already published page that visitors without an account see at `/` (with
  “Sign in” at the top right). Signed-in users still see their dashboard.
- **Published** (own tab): all public pages with account, name and date – **Withdraw** ends a publication
  immediately (after a confirmation). The user sees “Withdrawn by the admin on …” in the dialog; address and settings
  are kept.

Every error case – unknown name, private page, publishing turned off – returns the same neutral “Not found” page.
Public pages work without JavaScript and without cookies; each IP address may make 120 requests per minute
(`PUBLIC_RATE`). If Saga runs behind a reverse proxy, add it to `TRUSTED_PROXIES` (section 3) – otherwise all
visitors share a single limit.

**Custom domain:** To show the public pages on e.g. `blog.example.org`, point the domain's DNS to your server,
forward it to Saga in the reverse proxy (as in section 3) and add it to `.env`: `PUBLIC_HOSTS=blog.example.org`
(then run `docker compose up -d`). `BASE_URL` stays your main address. On the custom domain Saga only serves the
home page for visitors (`/`, choose one in the admin area), the `/@…` addresses and the files they need – sign-in
and everything else redirects to `BASE_URL`. When opened via that domain, the pages also name it as their main
address (`canonical`). Example for Caddy:

```
blog.example.org {
    reverse_proxy 127.0.0.1:8080
}
```

### Sharing and comments

Pages and databases can be shared with **other accounts** and **groups** on the same Saga instance. Use the
**“Share”** button at the top right of your own page (or the page menu **⋯** → “Share …”): search for a person or a
group (from 3 characters also parts of names, with 1–2 characters exact matches only, e.g. “hd”; you see display
name and username, never a list of all accounts), pick a role and click
the match. The dialog only offers groups you belong to – local groups as well as groups from LDAP or SSO. In the list
below you change the role or remove access (with a confirmation; access ends immediately). With an empty search field
your groups appear as chips (“Your groups:”), and a line under the field explains what the chosen role may do. The
list shows each person’s actual role: if someone has more rights through a group, it says so (“Can comment (via team:
Can edit)”), and the confirmation when removing names the access that remains. Shares from parent pages appear in a
separate, read-only section with a way to the parent page. At the bottom you can copy the page link – it opens the
page for everyone who has access.

| Role | may |
|---|---|
| **View** | read the page, subpages and database entries, export, read comments |
| **Comment** | additionally comment, reply and resolve threads |
| **Edit** | additionally change title, content, icon, cover, tags and properties, create subpages and entries, upload images |

- **Subpages are included:** a share also applies to all subpages and to the entries of databases below. The highest
  role counts (person or group, this page or a page above it). Archived pages, pages in the trash and templates are
  only visible to the owner.
- **Only the owner may:** delete, archive, move freely, lock, mark as template, publish, manage sharing. Since 2.3,
  people with “Edit” may view and restore versions (the previous state is kept as a version) and move pages **within the
  shared area** – only to places with exactly the same shares, so nobody gains or loses access (“⋯” → “Move to”).
  Whatever others create or upload belongs to the page owner; versions are kept with the owner. So people
  with “Edit” use the **owner’s storage** – their images and files count towards it.
- **Sharing and publishing:** if a page is public with “Also publish subpages” and is also shared with “Edit”,
  subpages and images those people create become public immediately – the publish dialog points this out.
- **Shared with me:** in the sidebar, the section “Shared with me” lists the pages others have shared with you
  (with the owner’s name). Your own shared pages show a people icon in the page tree. New comments by others show as a small
  dot on the page in the tree, as a number on “Shared with me” and as “💬 n” on the “Pages” button on the home page.
  If sharing is turned off in the admin area, a notice says so.
- **Who changed what:** below the title you see “Last changed by Name · 5 min ago” (if it wasn’t you) and, for pages
  someone else created, “Created by Name”. The version history names the author of every saved version.
- **Other people’s pages** show “Shared by Name · You can view/comment/edit” below the title (and below that, who
  changed it last). If the owner changes your role, the page shows it the next time it saves or when you return to the tab. With “Can view” or “Can comment” the editor is
  read-only; databases show “View only” (sorting and filtering work but are not saved). Task, calendar and Immich
  blocks only show “Only visible to the owner” – never the owner’s data or yours. Links to pages that are not shared
  with you appear as “Page without access”.
- **Editing at the same time:** if two people save almost simultaneously, the familiar conflict notice appears – with
  a name: “Bert just changed this page”. **“Load new version”** loads Bert’s version and shows your unsaved text for
  copying. **“Keep my version”** asks first; Bert’s version then stays in the owner’s version history – nothing is lost
  without a trace.
- **Access ends while you type:** if the owner removes your access (or lowers it to “Can view”, or the admin turns
  sharing off), a dialog shows your latest text for copying. Old links to pages you can’t access show “No access to
  this page” with “Try again” and “Go to “Shared with me””.

**Comments** are attached to a block and stay unobtrusive: the margin only shows a small number (💬 2), and only
while there are open threads. Comment via the block menu (**⋮⋮** left of the block → “Comment”, or Ctrl/⌘ + / →
“Comment”); hovering over a block also shows a small 💬+ button (on touch devices with read-only pages: tap the block first). The thread opens in a sidebar on the
right (on phones from the bottom). There you reply, edit or delete your own comments (the page owner may delete any)
and close a thread with **“✓ Resolve”** – it collapses and disappears; the **“Show resolved”** switch brings it back,
“Reopen” makes it active again. “💬 3” in the page header opens all threads of the page; a small dot marks unread
ones. Editing and deleting a comment are in its ⋯ menu; the owner also finds all threads in the page menu **⋯** →
“Comments” (with the number of resolved ones). Comments are plain text (at most 2000 characters, line breaks allowed; links are detected), at most 30
comments per minute. You can comment on your own, unshared pages too – as a note to yourself. If a block is deleted,
its thread stays in the list as “Block removed”. If you delete the first comment of a thread that others have
replied to, the replies remain and your comment shows as “Comment deleted”; the page owner can delete the whole
thread (after confirming).

**For the admin** (admin area → General → “Share”): **Allow sharing** (on by default; off = all shares stop working
immediately but stay saved) and **Only within shared groups** (off by default; people search and sharing only with
accounts that have a group in common with you; it only affects new shares – existing ones remain if you turn it on
later). Saga takes over groups from LDAP or SSO at sign-in: if a membership changes there, shares with groups reflect
it from the account’s next sign-in. The user detail page only shows the number of shares, never content.
When an account is deleted, its shares and the shares given to it disappear; its comments on other people’s pages
remain as “Deleted account”. When a group is deleted, shares with it end.

### Sharing with other Saga instances (federation)

Several Saga servers (e.g. yours and a club's) can be connected. After that, a page can also be shared with
**name@server** – a person there (via their public name, see “Publishing pages”) or a group there
(**group@server**). Content always stays on the owner's server: the other person reads, comments and edits through
their own Saga server, which asks the owner's server in the background. The owner's server always decides the role –
revoking access takes effect immediately. Everything uses the normal HTTPS port; there is no extra service.

**Connecting (admins of both servers):**
1. Admin area → **Federation** → “Turn on federation” (default: off). It shows your address and your **fingerprint**
   (a short form of this server's public key).
2. Server A: **“Create invite code”** – give the address and code to the admin of server B (the code is valid for 24
   hours and can be used once).
3. Server B: enter the address and code under “Connect to an instance” → Saga shows A's fingerprint. If it matches the
   one A gave you (ideally compare it through a second channel) → “Fingerprint matches – connect”.
4. Server A: the request appears under “Connected instances” with B's fingerprint → **Confirm**. Both are now
   connected. If B couldn't be reached at that moment, use “Check status” there.

**Disconnecting** (either side, any time) takes effect immediately: all shares with people and groups of the other
server end, and pages from there disappear. The **audit log** shows connections, shares across servers and rejected
requests – never content. Saga only reaches internal addresses (192.168.x, 10.x …) if they are listed under General →
“Allowed internal hosts”; other servers only connect over https.

**Sharing:** in the share dialog, enter `name@server` (or `@name@server`) – Saga asks the connected server whether the
person or group exists and shows them with their origin (“Bert (bert@club.example)”). With servers on 2.3 you can also
find people there by their display name (address book) – sharing then also works with people who have no public name
(via a random, permanent ID, never the sign-in name). With older servers you need a public name (Settings → Account).
People from other servers always appear with `@server` in “Shared with me”, comments and notifications, so nobody is
mistaken for an account here with the same name. If “Share only within common groups” is on, sharing with other servers
isn't possible – except with servers for which the admin has turned on the exception (see below).

**Receiving shares:** pages from other servers are listed under **“Shared with me”** (with `name@server`), open in the
normal editor and can be read, commented on and edited depending on the role (title and content; editing at the same
time triggers the usual conflict notice). Images come through your own server. With servers before 2.3 you need a
public name for this. Notifications (“shared a page with you”, “replied”) also work across servers – without the
comment text.

**Protocol 2 and features per server (2.3):** connected servers running Saga 2.3 negotiate what both of them support
when connecting (and every 6 hours after that); servers on 2.1 stay connected as before (sharing, comments,
notifications). **Admin → Federation → “Features per instance”** lists the protocol, the common capabilities and the
switches for every connected server:
- **Chat** (direct chats across servers, see below) and **calls** – on by default.
- **Share the address book with this instance** – on by default. People there can then find people from here by their
  display name (chat and share search, “Anna Berg · b.example”). Only the display name, the profile picture and a
  random, permanent ID (`~…@server`) are transferred – never sign-in names, email addresses or groups. Optionally only
  people from certain groups. Accounts without a display name, deactivated accounts and everyone who turns off
  “Appear in the address books of connected servers” under Settings → Account never appear. Turning it off or
  disconnecting deletes the address book on both sides immediately.
- **Exception from “Share only within common groups”** – off by default. If that restriction (General) is on, shares,
  address book and chats with other servers are blocked; with the exception they are allowed for this server.
Events between servers go out immediately (“express”, at most 5 s wait, `FED_EXPRESS_TIMEOUT`); if the other server
can't be reached, the event waits in the outbox and is delivered later in the right order (for up to 3 days). For
scripts the admin area offers `GET /api/federation` and `PUT /api/federation/peers/{id}` (`{"chat": false, …}`, admin
session and the `X-CSRF-Token` header).

**Chats and calls across servers (2.3, both servers 2.3):** in Chats, under **“+ New”**, type the name – people on
connected servers appear with their origin (“Bert Guest · club.example”). One click starts the direct chat; the other
person sees it as a **message request** at the top of the list – without a notification, push or ringing until they
**accept**. They can also **decline** or **block** (the person or the whole server); the other side isn't told.
Blocked people are listed under “Chats & devices”, where you can unblock them. Messages stay end-to-end encrypted; the
servers only see encrypted data.
- The server of the person who starts the chat is its **home**: it decides the order and distributes messages,
  deletions, “Delete automatically after …” and the AI switch. If it is briefly unreachable, a message waits on your
  own server (“⏳ being delivered”) and goes out later in the right order (for up to 3 days, otherwise “⚠ not
  delivered” with “Send again”).
- **Attachments** stay on the sender's server; the other side fetches them when opening them (thumbnails right away).
- The **safety number** and “Mark as verified” work as in local chats. If the other person's key changes, a warning
  appears until you confirm it; if they were marked as “verified”, sending is blocked until you have compared the number
  again.
- **AI in chats** is off in such chats until someone turns it on – everyone sees a notice in the chat.
- **Calls** work from accepted direct chats as usual; each side uses its own TURN server.
- **Disconnecting** the servers makes such chats read-only (the history stays readable) and ends running calls; if an
  account is deleted, its direct chats disappear on the other server too.
- Older apps only show such chats with the notice “please update the app”.

**Pages of other servers, complete (2.3, both servers 2.3):** shared pages from other servers can be used like your own
pages – depending on the role, which the page's server always checks:
- Open, filter, sort, group and page through **databases** (large ones too); with “Edit”, change values, columns and
  views and **create entries**. Relations to other shared databases on the same server work; links to pages that
  aren't shared with you are dropped.
- **Create subpages** and **move** them within the shared area (same shares, see “Roles” below).
- **Upload images and files** (editor, icon, cover image): they are stored on the page's server and count for the
  owner. On top of that, every person from another server has their own limit there (`FED_UPLOAD_QUOTA_MB`, default
  200 MB). Right after uploading only the uploader sees the file, after saving everyone with access.
- **Version history** (with “Edit”) and **export** (Markdown, with subpages as a ZIP).
- **“Who's here” and live updates:** while the page is open, your server keeps a short subscription with the page's
  server (expires after 90 seconds without renewal, `FED_WATCH_TTL`). Changes, presence and comment counts come through
  it – never content. New or moved subpages in shared areas update “Shared with me” by themselves.
- Not across servers: deleting, archiving, publishing, managing shares, backlinks, AI on the page.
With servers on 2.1, reading, commenting and editing title and content still work; new features then report “The
server … can't do this yet”. **Roles (also locally):** whoever may “Edit” now also sees versions and restores them
(locked pages still only the owner) and moves pages within a shared area – only to places where exactly the same shares
apply. Anyone who changes their public name or deletes their account loses shares from other servers (they are removed
there); the old name is blocked for other accounts.

**Groups and page chats across servers (2.3, all servers involved on 2.3):**
- **Free group:** in “+ New” → “+ Group with several people …” you can also pick people on connected servers. Your own
  server becomes the group's **home**; for the others it appears as a **message request** (accept, decline, or block
  the inviting person or their server). Admins of the group add or remove people – also from another server, the
  change always goes through the home. Every change shows up for everyone as a notice in the chat, with origin (“Anna
  Berg (a.example) added Bert (b.example)”).
- **Page chat:** if a page is shared with people or groups of other servers, they belong to its page chat (the page's
  server is the home). Members of a group on another server are reported only by that server – and only if the group
  is the target of a share there. You can also open the chat from a page of another server (“💬 Chat”). An existing
  page chat is switched to the new format; older messages stay readable for all previous members, and the AI is off
  afterwards.
- People on a third server that your server isn't connected to can still take part: keys and attachments come through
  the home, and your app checks the keys itself (safety number, warning when they change).
- **Disconnecting:** if the home disconnects from a server, that server's people are no longer members (notice in the
  chat); on the disconnected server, the chat becomes read-only.

**For admins: overview, privacy, error messages**

*In short:* turn on federation → create an invite code → the other admin connects and compares the fingerprint →
confirm. Then decide per server under **“Features per instance”** what is allowed: **chat**, **calls**, **share the
address book with this instance** (optionally only certain groups) and the **exception from “Share only within common
groups”**. Every change takes effect immediately; “Disconnect” ends everything with that server.

*Privacy – what a connected server learns from here:*
- When sharing and commenting: the public name or a random, permanent ID (`~…`) and the display name of the acting
  person, the groups it named itself as the target of a share, and the content of the pages shared with people there.
  **Never** sign-in names, email addresses, passwords or other groups.
- In the address book (only if the switch is on): display name, profile picture version, ID and – if set – the public
  name. Anyone who turns off “Appear in the address books of connected servers” under Settings → Account disappears
  there immediately; the other servers then also delete the stored name unless the person still appears there
  (comments and chat histories keep it).
- Chats and calls: only encrypted data and the IDs of those involved – no server sees content.
- **Disconnecting** deletes shares, address book, key cache, waiting events and names of people on the other server,
  as far as they no longer appear here.
- Mention in your privacy policy which servers you are connected to and that display names are transferred there
  (address book).

*Protection against a faulty or malicious server:* every request and response is signed; Saga checks sender, order and
signatures itself, throttles floods per server (requests, events, messages, name lookups, uploads) and lets a slow
server occupy only a few background jobs – the other servers don't notice anything. Whatever was rejected is listed in
the **audit log** (without content).

*Error messages:*

| Message / audit log entry | Meaning and what to do |
|---|---|
| “Address not allowed (internal address …)” | The other server has an internal address: add it under General → “Allowed internal hosts” |
| “not a Saga instance with federation” / “unreachable” | Federation is off there, wrong address, certificate/proxy problem – check the address in the browser, then “Check status” |
| “Fingerprint changed” when connecting | The server reports a different key than expected – do **not** connect, sort it out with the other admin |
| “No additional capabilities” / protocol 1 | The other server runs 2.1: sharing and comments work, chats, address book & co. only after its update |
| “Fell back to protocol 1” | A server with protocol 2 suddenly reports protocol 1 (older version restored – or an attack). Chats with it pause, waiting events stay in the outbox until it reports protocol 2 again |
| “Other server is throttling (too many requests)” | The other server is slowing us down: Saga waits the time it names and then delivers everything |
| `event.stuck` / `event.dropped` | An event is rejected repeatedly or was dropped after 3 days – usually a version difference or an error on the other server |
| `sig.rejected` / `sig.replay` | Request with a wrong signature or a repeated one (check both servers' clocks – a difference of more than 5 minutes is enough) |
| `chat.forged`, `chat.badsig`, `chat.replay`, `chat.reorder`, `chat.keymismatch` | The other server sent forged, wrongly signed, repeated or reordered chat data – rejected. If they pile up, check or disconnect the connection |
| `chat.gap` | Messages were missing – they were fetched again from the home (rare with a healthy server) |
| `chat.rate` | Flood from a server throttled (messages, requests, member lists, name lookups) – nothing is lost, it arrives later |
| `chat.cache` | Cached copies of attachments from other servers over the limit (`FED_CHAT_CACHE_MB`) – the least recently used ones were removed and are fetched again when needed |

### Notifications and live updates

Saga keeps the dashboard and open pages up to date – without reloading.

- **Bell** at the top right (dashboard and pages): the number shows unread notifications; a click opens the list (from
  the bottom on phones). You get a notification when someone shares a page with you or changes your role, when someone
  comments on your page, when someone replies in a thread you're part of, and when an admin withdraws one of your
  published pages – never for your own actions. Several comments in the same thread within 10 minutes are combined
  (“3 new replies in a thread”). A click opens the page (for comments, the thread right away) and marks the entry as
  read; **“Mark all as read”** marks everything. Title and comment excerpt are only shown while you have access – after
  access is removed, the entry says “No access anymore”. Notifications are kept for 90 days.
- **Settings → Account → Notifications:** turn each kind on or off.
- **Who's here:** if others have the page open, their initials appear in the page header (“Bert is also here”); a ring
  means “Bert is editing”.
- **The page changes while you have it open:** without unsaved changes, Saga quietly loads the new version (scroll
  position and cursor stay where they were, as far as possible). If you're typing yourself, you see “Anna changed this
  page” with **Reload** instead; if you save anyway, the familiar conflict notice takes over.
- Comments, the unread dots, “Shared with me” and “💬 n” on the “Pages” button update instantly as well.
- Without a live connection (proxy buffers, more than 5 open tabs of the same account) Saga checks every 60 seconds.
  For the reverse proxy, see section 3; the admin area shows the number of active live connections under General.

### Chats (end-to-end encrypted)

The **“Chats”** button in the header of the dashboard and pages (with the number of unread messages) opens chats in a
separate tab. All messages, attachments and the names of custom groups are encrypted **on your devices** – the server
only stores encrypted data. The admin can't read any content either.

- **Setup (once per account):** the first time you open chats, Saga creates your keys and shows a **recovery key**
  (32 characters, e.g. `ABCD-EFGH-…`). Write it down or keep it in a password manager (“Copy”, “Save as file”) and
  confirm the last four characters. Without it and without another set-up device your messages are lost – the admin
  can't restore them either.
- **New device or browser:** either **“Confirm with another device”** – on a device where chats already work, the request
  appears with a 20-digit code (four groups of five); confirm it only if it fully matches the code on the new device – or **enter the recovery key**.
  Afterwards the new device can also read the previous history. If you have neither, you can **start over** (new keys;
  old messages can no longer be read). Saga asks for your password first; SSO accounts sign in again briefly
  (then you have 5 minutes).
- **Conversations:** **“+ New”** → search a person (short names like “hd” exactly, otherwise parts from 3 characters) for a **direct chat**, one of your groups for a
  **group chat** (members follow the group, also from LDAP/SSO) or “group with several people” for a **custom group**
  (the creator invites and removes; the name is encrypted). On shared pages **“💬 Chat”** in the page header opens the
  **page chat** with everyone who can see the page – when the shares change, the members change. New members only see
  messages from when they joined; people who leave get nothing new.
- **Writing:** Enter sends, Shift+Enter starts a new line. 📎 attaches images or files (encrypted, up to the usual
  attachment size); **@** links a page – others only see its title if they can see the page themselves. Images show up in the history as a small
  preview (created when sending, encrypted as well); the original only loads when you tap it. Loaded images are kept
  encrypted in the browser (up to 200 MB, cleared when you sign out). Via “⋯” on a
  message: **reply**, copy text, **delete for everyone** (your own messages). “is typing …” and new messages appear right
  away (live channel).
- **Chat menu (⋯ at the top):** **safety number** (60 digits, identical on both sides – compare in person or by phone
  and “Mark as verified”; if it changes, the chat warns you), members, **mute** (no notification, not counted as
  unread), **delete messages automatically after** 1 hour … 1 year (applies to new messages), rename or leave a custom
  group. The magnifier searches loaded messages – on this device only.
- **Archive and delete:** in the chat menu (⋯) or by right-clicking or long-pressing a chat in the list. **Archive**
  moves the chat to the **“Archived”** section at the bottom of the list – it no longer counts as unread and comes
  back by itself with the next message (muted chats stay archived). **Delete chat …** asks: **Just for me** – the
  history disappears only for you, on all your devices (the server deletes the keys for it); a direct chat leaves the
  list until a new message arrives, other chats are archived; you can also leave a free group at the same time.
  **For everyone** – direct chat (both people) and free group (its admins): the chat is gone for everyone with all
  messages, attachments and calls, the others see “Name deleted the chat”. Group chat (Saga admins) and page chat (the
  page owner): only the history is deleted, the chat stays. Neither can be undone; anyone who has already seen messages
  may have copies. API for apps: `docs/APP-API-CHAT.md`.
- **⚙ Chats & devices:** devices with access, remove devices (they get nothing more, immediately), create a new
  recovery key (the old one stops working).
- Notification “New message from Name” – without content; can be turned off under Settings → Account → Notifications.

**Admin:** General → “Chats”: **Allow chats** (on by default) and **Keep messages at most (days)** – the server deletes
older messages and attachments hourly, even if a chat has a longer period. The user page only shows counts (chats,
devices). Deleting an account removes keys, devices, memberships, sent messages and attachments; direct chats with the
account disappear entirely. Limits: 64 KB per message, 60 messages per minute, 10 devices per account, 50 members per
chat, **storage for chat attachments per account** (default 4096 MB, `CHAT_QUOTA_MB`) and **largest video in chats**
(default 500 MB, `VIDEO_MAX_MB`) – both adjustable in the admin area under “Chats”. No extra port.

**Videos (2.4):** videos show a poster with the duration in the conversation and play by streaming (seeking without a full
download). The sending device creates poster, duration and dimensions; the server sees none of it and never transcodes
(impossible with end-to-end encryption anyway). From phones videos are sent “optimized 720p” by default, “Original” can be
chosen. Videos above `ATTACH_MAX_MB` use the block format `saga-file/v2` with a resumable upload (parts up to 8 MB – the
proxy limit from section 3 is enough); older apps show them as an attachment with the hint “Open with Saga 2.4”. Uploads
in progress count against the storage and expire after 24 hours. Across servers, your server fetches large videos only
range by range from the sender's server (cached copy, `FED_CHAT_CACHE_MB`); with servers on Saga 2.3 only attachments up
to `ATTACH_MAX_MB` work.

**Technical (format v1):** libsodium only (browser: libsodium.js, bundled in `static/vendor/libsodium`). Per account an
identity key (Ed25519) and a backup key (X25519); per device X25519 + Ed25519, signed by the identity key. Each message:
random key, XChaCha20-Poly1305 (`crypto_aead_xchacha20poly1305_ietf`), the key sealed with `crypto_box_seal` for every
device and the backup key of every member, signed by the device (`crypto_sign_detached`). The key bundle on the server is
encrypted with a key derived from the recovery key via `crypto_pwhash` (Argon2id). Exact format: header of
`saga/chat.py`; test vectors for your own clients: `tests/chat_vectors.json` (check with
`python3 tools/chat_vectors.py check` or `node tools/chat_vectors.mjs`). In the browser the private keys live in
IndexedDB; chats don't work in a private window.

### Calls (audio, video, screen)

In a **direct chat** there are two buttons at the top: **📞 Call** and **🎥 Video call**. Saga connects the two devices
directly (WebRTC) – media does not go through the Saga server and is **end-to-end encrypted**. No extra port: Saga sets
up the call over the regular web port (live channel + normal requests).

- **Ringing:** the other person sees "Name is calling" with Answer/Decline on **every Saga page** (dashboard, pages,
  chats) – for video calls also "Answer with audio only" – plus a ringtone (🔔/🔕 turns it off for this browser) and a
  **push notification** to phone/computer ("Call from Name", or without the name if "Show names" is off). All devices
  and tabs of the person ring; answering on one stops the others. Answering on the dashboard or a page opens the call in
  the chats tab (that's where this browser's keys are).
- **During the call:** large picture of the other person, small own picture, **mute**, **camera on/off** (also later in
  an audio call), **share screen** (on a computer), **hang up**; "▭" shrinks the window to a corner (keep chatting).
  At the top: duration, connection quality and the path – **direct** or **via relay (TURN)** – and "🔒 End-to-end
  encrypted" once the check (below) has passed.
- **Sharing your screen (2.4 and later):** before the first share, Saga reminds you that everything in the selection is
  visible – including notifications and other windows (tip: share a single window or tab). While sharing, a red bar
  "You are sharing your screen" with **Stop sharing** stays on top. If both sides run Saga 2.4, the camera keeps running: the
  other person sees the screen large and the camera small (tap it or ⇆ to swap). In Chrome/Edge the tab audio (on
  Windows also system audio) can be shared. Nothing is recorded or stored on the server.
- **Another device (2.4 and later):** while a call runs on your phone, your other devices show "Call in progress on
  <device>". **Add** brings the laptop in as a **companion device** – without microphone and sound (no echo in the same
  room), e.g. to share the screen from there. **Move here** moves the call to this device without interruption: the new
  device connects, takes over sound and microphone, the old one goes quiet. The companion device has the same button.
  Companion devices are visible to everyone in the call.
- **Group calls (2.4 and later):** in group, free and page chats 📞/🎥 start a **group call**. All members with Saga 2.4
  ring (in chats with more than 8 members only a silent "Group call in progress – Join"); you can join and leave at any
  time, the call runs as long as at least two people are in it. At most **4 devices with video** or **6 audio only**
  (your extra devices count). Everyone appears as a tile, the current speaker gets a green frame; screen sharing works as
  in direct chats. End-to-end encrypted as before (each connection separately, the server sees nothing). People with an
  older app see "needs Saga 2.4"; across servers it only works when all involved Saga servers run 2.4.
- **Not answered:** after the ring duration (default 45 s) the call counts as **missed**. The chat then shows "Missed
  call" (with "Call back") and there is a notification "Missed call from Name" (can be turned off under notifications or
  push → "Calls"). Ended, declined and busy calls also appear in the chat.
- **Busy:** someone already on a call doesn't get a second one – the caller sees "Busy".
- **Who may call?** Only the two people of a direct chat, both with chats set up. Muted chats don't ring the phone.
  Against ring spam: at most 3 calls per minute to the same person.
- **Camera/microphone:** the browser asks on the first call. If it was denied, Saga explains where to allow it again
  (lock icon next to the address). Calls need `https`.

**Reachability:** within the same network and between typical home connections, the direct connection almost always
works – for that the devices ask a **STUN server** for their public address (default: `stun:stun.nextcloud.com:443`).
Behind strict corporate firewalls, in some mobile networks (CGNAT) or when one side may only use port 443 outbound, you
need a **TURN server** that relays the media (still encrypted – the TURN server cannot listen in). Without it people see
"Direct connection not possible … The admin can configure a TURN server."

**Admin → General → "Calls":** **Allow calls** (default on; requires chats), **STUN server** (empty = none; privacy: the
STUN server sees the callers' IP addresses – if you don't want that, use your own, e.g. your coturn), **TURN server**
with a **shared secret** (recommended: Saga creates per-call credentials following the TURN REST scheme, valid for 12
hours; the TURN server only sees a hash instead of the account name) or a **fixed username/password**, **Connect via
TURN only** (the devices then don't see each other's IP address) and the **ring duration** (15–120 s). Secrets are
stored encrypted; "Delete TURN credentials" removes them.

#### TURN over port 443 (coturn behind the reverse proxy, via SNI)

Saga opens no port – and TURN doesn't need one either: when a connection starts, the reverse proxy looks at the name in
the TLS handshake (SNI) and passes `turn.example.org` unchanged to coturn, everything else to the web servers as
before. You only need one more DNS name (and a certificate for it).

1. Install **coturn** (package `coturn` or container `coturn/coturn`) and `/etc/turnserver.conf`:

```ini
use-auth-secret
static-auth-secret=A-LONG-RANDOM-SECRET                  # e.g. openssl rand -hex 32 - enter the same in the Saga admin
realm=turn.example.org
listening-ip=127.0.0.1                                   # only the proxy talks to coturn
tls-listening-port=5349
cert=/etc/coturn/fullchain.pem                          # certificate for turn.example.org
pkey=/etc/coturn/privkey.pem
fingerprint
no-cli
no-multicast-peers
# no relaying into your own network (prevents abuse as a tunnel) ...
denied-peer-ip=10.0.0.0-10.255.255.255
denied-peer-ip=172.16.0.0-172.31.255.255
denied-peer-ip=192.168.0.0-192.168.255.255
# ... except to your own relay addresses (both sides via TURN): this server's IP
allowed-peer-ip=192.0.2.10                               # example IP - replace with your own
relay-ip=192.0.2.10
```

2. Split port 443 by SNI in the **reverse proxy**. The existing HTTPS servers then listen internally (here 8443).

nginx (module `stream`, `ssl_preread`):

```nginx
stream {
    map $ssl_preread_server_name $backend {
        turn.example.org  127.0.0.1:5349;     # coturn (does TLS itself)
        default           127.0.0.1:8443;     # all previous https servers: "listen 127.0.0.1:8443 ssl;"
    }
    server {
        listen 443;
        listen [::]:443;
        ssl_preread on;
        proxy_pass $backend;
    }
}
```

HAProxy:

```
frontend tls443
    bind :443
    mode tcp
    tcp-request inspect-delay 5s
    tcp-request content accept if { req.ssl_hello_type 1 }
    use_backend coturn if { req.ssl_sni -i turn.example.org }
    default_backend web
backend coturn
    mode tcp
    server turn 127.0.0.1:5349
backend web
    mode tcp
    server web 127.0.0.1:8443
```

Traefik (TCP router with TLS passthrough, entry point `websecure` = 443):

```yaml
tcp:
  routers:
    turn:
      entryPoints: [websecure]
      rule: "HostSNI(`turn.example.org`)"
      service: coturn
      tls:
        passthrough: true
  services:
    coturn:
      loadBalancer:
        servers:
          - address: "127.0.0.1:5349"
```

Caddy can only do this with the `caddy-l4` plugin (matcher `tls sni turn.example.org` → `proxy 127.0.0.1:5349`).

3. In the **Saga admin**: TURN server `turns:turn.example.org:443?transport=tcp`, access "Shared secret" and the same
   secret as in coturn. Optionally "Connect via TURN only".
4. **Check:** call between a device on Wi-Fi and a phone on mobile data – the call window then shows "via relay
   (TURN)". Without a call: `turnutils_uclient -t -S -p 443 -W <secret> turn.example.org` (from the coturn package).

Notes: TURN over TLS/TCP on 443 gets through almost any firewall but adds some latency. Relaying between two devices
happens inside coturn (both sides get a relay address) – so the relay port range (`min-port`/`max-port`) does not have
to be reachable from outside. If you can also open UDP 3478, add `turn:turn.example.org:3478` as a second address
(faster where UDP is allowed).

**Technical (format `saga-call/v1`):** WebRTC with DTLS-SRTP. Every signalling message (SDP offer/answer, ICE
candidates, state) is signed by the sending **chat device** with its Ed25519 key – over
`LP("saga-call/v1/signal", call_id, from_account, from_device, to_account, to_device, body)` – and sealed with
`crypto_box_seal` for the target device. The server sees neither SDP nor IP addresses and cannot inject its own DTLS
key: the SDP containing the DTLS fingerprint is signed, the device certificate is tied to the identity key (safety
number in the chat), and after connecting the browser compares the peer's fingerprint once more with the signed SDP.
If anything doesn't match, the call is aborted with a clear message. Endpoints: `/api/calls/config`, `POST /api/calls`,
`/api/calls/{id}` (`answer`, `decline`, `signal`, `end`, `alive`), `/api/calls/active`; live events `call.ring`,
`call.signal`, `call.state`. Test vectors for your own clients: `tests/call_vectors.json`
(`python3 tools/call_vectors.py check`, `node tools/call_vectors.mjs`). Calls appear in the chat as an entry with
metadata (kind, result, duration – only what the server knows anyway); apps get them with `?calls=1`. Push: kind
`call` with high priority and short lifetime (ntfy priority 5), for the iOS/macOS app additionally Apple VoIP push
(PushKit, subscription type `apns-voip`, topic `<bundle-id>.voip`). The access log contains no IP addresses for
`/api/calls…`.

**Later (group calls):** planned as a mesh of up to about 4 people in a group chat (each person builds its own,
equally verified connection to every other; signalling as above per pair). Beyond that a forwarding server (SFU) with
additional content encryption (Insertable Streams/SFrame) would be needed – deliberately not part of Saga yet.

### Push notifications to phone and computer

The bell shows everything while Saga is open. For when you're away, Saga sends **push notifications** – even when
Saga is closed. Turn them on under **Settings → Push notifications**:

- **This browser:** “Turn on in this browser” and allow the browser's prompt (Chrome, Edge, Firefox, Safari). On
  **iPhone and iPad** this only works once Saga is on the home screen (Share → “Add to Home Screen”) – open it from
  there and turn it on. Requires `https`. Signing out in this browser ends the subscription; “sign out everywhere”
  and a password change end all browser subscriptions.
- **ntfy:** In the [ntfy](https://ntfy.sh) app (Android, iPhone, Linux), subscribe to a topic with a hard-to-guess
  name and enter its address under “Add ntfy” (e.g. `https://ntfy.sh/my-secret-topic`, with an access token for
  protected topics). The admin allows your own ntfy servers on the home network under General.
- **Apps:** the Android and Linux apps register via UnifiedPush, the iOS/macOS app via Apple Push (APNs, set up by the
  admin). They appear in the device list.
- **Devices:** all browsers, ntfy topics and apps with “Test” and “Remove”; “Last error” helps when nothing arrives.
- **About what?** Turn sharing, comments, chat and publishing on or off individually. **Show names** off → only
  “New comment on your page” instead of “Jonas commented on your page”. **Quiet hours** (e.g. 22:00–07:00, in your
  time zone): no push notifications during that time; the bell still has everything.

Privacy: push notifications contain only a short sentence and the link – never page titles, comments or chat
content. Browser notifications are end-to-end encrypted (Web Push, RFC 8291): the browser's push service (Google,
Mozilla, Apple, Microsoft) only sees encrypted data. **ntfy** (and UnifiedPush without encryption) **never gets
names**, because the ntfy server can read the text. Before delivering, Saga checks that you can still see the page.
Saga only connects **outbound** – no extra port.

**Admin → Push notifications:** turn channels off individually (Web Push, ntfy, UnifiedPush) and set up Apple Push:
in the Apple Developer account under *Certificates, Identifiers & Profiles → Keys*, create a key with “Apple Push
Notifications service (APNs)”, upload the `.p8` file (offered only once) and enter Team ID, Key ID and the app's
bundle ID. The key is stored encrypted. Without a key, Apple Push stays off. Saga creates the Web Push key (VAPID)
itself when first needed; it depends on `SECRET_KEY`.

### AI assistant (optional)

When the assistant is turned on, the pages view shows **Ask Saga AI** at the top (or the name the admin chose, e.g.
“Ask Marin”) with its profile picture. It works with a language model the
admin sets up (e.g. Ollama on your own network). **Everything you give the assistant – your question and the matching
excerpts from pages – is sent to this endpoint**; its address is shown at the bottom of the side panel.

- **Ask your pages:** Saga searches the pages *you* are allowed to see (your own and those shared with you; not trash or
  archive), sends the best-matching excerpts along and shows the answer as it is written. The numbers **[1]**, **[2]**
  lead to the source pages; all sources are listed below. Links to pages you can't see are only sent as “Page without
  access”. The assistant only includes **tasks** and **calendar** when you turn on the switches below the input field.
  It never reads chats (end-to-end encrypted) or pages
  from other Saga instances (federation).
- **This page** (also in the page menu “…” → AI assistant): **Summarize**, **Suggest tasks** (tick the ones you want →
  “Add to tasks” or “Insert as checklist”), **Improve selected text** and **Translate to …** for the blocks you have
  selected. The result is always just a **suggestion**: “Replace” (after confirming), “Insert below” or “Copy” – the
  assistant itself can't change anything. Replace and insert are only offered if you may edit the page; earlier
  versions are in the version history.
- **History:** off by default. Under “History” you can turn it on and delete single entries or everything; turning it
  off deletes the saved history. Sources you no longer have access to don't show a title.
- The admin can limit the number of requests per day (“12 of 50 requests left today”). Only one request per person runs
  at a time; **Stop** cancels an answer.

**For admins:** admin area → **AI assistant**. Default: off.

| Field | Meaning |
|---|---|
| Provider | *OpenAI-compatible* (`/v1/chat/completions` – OpenAI, Ollama, LM Studio, vLLM …) or *Ollama* (native API `/api/chat`) |
| Address | e.g. `http://ollama.lan:11434/v1` (OpenAI-compatible) or `http://ollama.lan:11434` (Ollama). `http` only for internal addresses |
| Model | e.g. `llama3.1:8b` – “Test connection” lists the models offered and fetches a short answer |
| API key | optional, stored encrypted (like LDAP/OIDC); leave empty = keep |
| Only for these groups | empty = all accounts, otherwise only members (local, LDAP or SSO groups) |
| Time limit, requests per person and day, page content per request | limits (0 requests = unlimited) |
| Allow history | people may turn on their history |

Saga only makes **outgoing** connections to exactly this address – no extra port. Internal addresses (LAN Ollama) are
allowed, but only with the IP addresses resolved when saving (“Pinned addresses”); if the address changes in DNS, save
once more. Saga doesn't follow redirects. `AI_MAX_PARALLEL` limits concurrent requests overall (default 4). Questions,
content and answers never end up in the log.

To protect against injected instructions (“Ignore all rules …” inside a page), Saga marks page content as data for the
model and gives the assistant no write access at all. A language model can still be wrong or be influenced by page
content – so check answers, especially for shared pages.

**Persona:** under admin area → AI assistant → **Persona** the AI gets a name (empty = “Saga KI” / “Saga AI” / “Saga IA”
depending on the language), a **profile picture** (PNG, JPEG, GIF, WebP or HEIC; checked like profile pictures, cropped to
a square and re-encoded – without a picture Saga shows a neutral ✦ symbol) and optionally a short **personality and tone**
(at most 500 characters). The tone only shapes the wording of the AI's own answers; rules, source references and formats
take precedence, improving and translating keep the tone of the text. Web and apps show name and picture wherever the AI
appears and pick up changes immediately.

**AI in chats – only when mentioned:** if the admin turns on “Allow AI in chats” (default: off), the AI can be allowed
in individual chats (chat menu “…” → “Allow Marin in this chat”: chat admins in free groups, both people in a direct
chat, Saga admins in group chats, the page owner in page chats). All members then see a notice at the top of the
chat. Writing **@Marin** (typing “@” suggests the AI) sends **that message and up to 12 messages before it in plain
text** to the AI endpoint – everything else stays end-to-end encrypted. The AI sees no pages, tasks or calendars.
The asker's device encrypts the reply like any message for all members; it appears with the AI's name and picture
(“asked by …”). The server stores neither question nor answer, only a checksum of the answer that lets the other
devices verify it really came from the AI. The assistant's quota and groups apply, plus at most 6 questions per minute.

**Web search for the AI:** if web search (section 10) is set up, the admin can turn on “Allow web search” under AI
assistant (default: off). The AI then searches the web itself for questions about current things – weather, news,
opening hours: the model first decides whether a search is needed and forms at most two short search terms, which go to
search engines via SearXNG. Saga also reads the text of the best results (public addresses only). The answer names the
web sources with links; meanwhile it shows “Marin is searching the web …”. In the assistant you can switch it off per
question with “Search the web”; summarize, improve, translate and suggest tasks never search the web. Each web search
counts as one more request in the quota.

### Account, calendars, Immich, apps

In the user menu at the top right under **Settings** (one tab each):

- **Account:** change your name, email and password (for LDAP/SSO accounts, at your provider). Changing your
  password signs out your other browser sessions **and all signed-in apps** – except this session and the app
  you're using right now. The apps then sign in once more. This is also where you change your **public name**
  (see “Publishing pages”).
- **Calendars:** connect CalDAV accounts (Nextcloud: `https://cloud.example.org/remote.php/dav/`).
  Ideally create a separate app password just for calendars.
- **Immich:** enter the address and an API key with the permissions `album.read`, `asset.read`, `asset.view`,
  `asset.download`, `user.read`. Your photos are then available as images and cover images.
- **Nextcloud:** link your own Nextcloud (Login Flow, its own app password; “Disconnect” revokes it) – for Nextcloud
  files in pages (see “Pages and databases”).
- **My backup:** back up your own workspace, encrypted, to **your own** target (see “Personal backup” in section 11).
- **Apps & devices:** a list of all signed-in apps. Each one comes with a small key for the widget, share
  extension and shortcuts; **Sign out** removes both. Keys created by hand (e.g. for scripts) are listed,
  collapsed, under "More keys".
- **Sign in with QR code:** "Apps & devices" shows a QR code that is valid for two minutes and can be used only
  once. Scan it with your phone's camera – the app takes over the server address and sign-in, without typing a
  password on the device. For the Mac and Linux apps there is **"Copy link"** below it: paste the pairing link
  into the app when signing in.
- **Signing in to an app via the browser:** The app opens Saga's sign-in page. If you're already signed in
  there, Saga asks first ("Sign in app") – only confirm if you just started the sign-in in the app yourself. If
  you sign in right then in that window (password or SSO), there is no extra question. **Cancel** takes you back
  to the app.

Public calendar and Immich servers (https) always work. **Servers on your home network** (e.g.
`192.168.1.20` or `nextcloud.lan`) must first be allowed by the admin under General – this keeps the internal
network from being queried through Saga. Saga connects directly to the checked address; an outgoing proxy
(`HTTPS_PROXY`/`HTTP_PROXY`) is **not** used for calendars and Immich.

![Settings](../screenshots/einstellungen.png)

### Passkeys and two-factor sign-in

Under **Settings → Account → Sign-in & security** you protect a local or LDAP account beyond its password (SSO
accounts do this at their sign-in service, see below):

- **Passkeys:** your browser or phone creates a key that lives in the device's password manager (iCloud Keychain,
  Google Password Manager, 1Password, Bitwarden …) or on a security key (YubiKey). With it you sign in **without
  any password** – "Sign in with a passkey" on the sign-in page, or straight from the suggestions under the user
  name – and confirm with fingerprint, face or device PIN. When you sign in with your password, the passkey acts
  as the second factor. You can add several passkeys (e.g. phone and laptop); each has a name, "created" and
  "last used".
- **Authenticator app** (TOTP: Aegis, 2FAS, Google/Microsoft Authenticator …): scan the QR code or type in the
  key, then enter a code. Saga asks for the 6-digit code after the password; every code works only once.
- **Recovery codes:** with your first factor you get 10 one-time codes – print them or keep them in your password
  manager; Saga shows them only once. They get you in when your phone or passkey is missing. "Generate new codes"
  invalidates the old ones.

**Signing in:** correct password → Saga asks for the second factor (passkey, code or recovery code). This step is
valid for 5 minutes and only in this browser; after 5 wrong codes there is a 15-minute pause. There is deliberately
no "trust this browser" option.

**Confirm before changes:** adding/removing passkeys, changing the authenticator app or generating new codes is
possible within 5 minutes of signing in – otherwise Saga first asks for your password, a code or a passkey. The
same applies to "Start chats over": a passkey or code now works instead of the password.

**Apps:** the apps sign in through Saga's sign-in page in the system browser window – the second step appears
there as well, and passkeys work in it on iPhone, iPad, Mac and Android. Apps that are already signed in stay
signed in, even when you turn on 2FA or the admin requires it. Pairing via QR code needs no second step (it starts
from a session that is already signed in). If the admin requires 2FA for everyone and your account has no factor
yet, a **new** app sign-in leads to the setup first ("Continue" then returns to the app); a pairing QR code is only
available afterwards.

**Requirement for passkeys:** `BASE_URL` with a host name and `https://` (e.g. `https://saga.example.org`; only
`http://localhost` works without https). If Saga is opened via an address other than `BASE_URL`, Saga explains
that instead of failing silently. Passkeys belong to the domain: if `BASE_URL` later moves to another domain,
existing passkeys stop working (signing in with password + code/recovery code still works). To make passkeys valid
for several subdomains, set `PASSKEY_RP_ID` to the common domain. In the **admin area** passkeys only work if
`ADMIN_URL` is on the same domain – otherwise use a code or recovery code there. That is why an admin without a
factor sets up an authenticator app there when the policy requires one (passkeys on the main site under
"Sign-in & security").

**SSO (OIDC):** Saga does not offer its own 2FA to SSO accounts – the sign-in service (e.g. Keycloak) asks for the
second factor. With the admin switch "Require a second factor for SSO", Saga only accepts SSO sign-ins if the
provider reports one (`amr` with `mfa`, `otp`, `hwk` …) or one of the configured `acr` values (which Saga also
requests from the provider).

**Lost your phone?** Sign in with a recovery code and set up the factor again. Without codes, the admin resets
the second factor (section 5) – or, if the admin cannot get in either, on the server
`docker compose exec saga python run.py 2fa-zuruecksetzen <name>` (section 2).

### Profile pictures

Instead of the colored initials, Saga shows a **profile picture** – in the header, on comments, in the presence
indicator of a page, in notifications, in the share dialog and people search, in chats (list, conversation,
members) and in the version history (“by …”). Without a picture, the initials stay.

- **Your own picture:** Settings → Account → Profile picture → “Upload picture …”. In the browser you choose a
  square crop (drag, zoom). PNG, JPEG, GIF, WebP and HEIC up to `AVATAR_MAX_MB` (default 5 MB) are allowed. The server
  re-encodes every picture (64/128/256 pixels, WebP and PNG/JPEG) and removes all metadata (EXIF with camera,
  location, date). “Remove my picture” deletes it again.
- **Gravatar is the default:** without a picture of your own – and with an email address on the account – you get
  your [Gravatar](https://gravatar.com). Privacy: **only the server** asks Gravatar (using the SHA-256 hash of the
  email address) and keeps the result for a day – including “no picture”. Browsers and apps never contact Gravatar
  themselves, so Gravatar does not learn who looks at a picture. Everyone can turn Gravatar off for themselves
  (“Use Gravatar if I have no picture of my own”), the admin for everyone (General → Profile pictures).
- **Picture from the identity provider (optional, admin):** “Use the picture from SSO” takes the address from the
  OIDC claim `picture` (fetched by the server, not the browser); “Use the picture from LDAP” reads `jpegPhoto` or
  `thumbnailPhoto` at sign-in. Saga only fetches internal addresses listed under “Allowed internal hosts”
  (SSRF protection); fetched pictures are limited to 2 MB and a few seconds.
- **Order:** own picture → picture from the identity provider → Gravatar → initials.
- **Who can see the picture?** Picture addresses contain a key that cannot be guessed (`/api/avatar/<key>`); only
  signed-in people (or apps) can load them, and accounts cannot be enumerated this way. On **published pages** your
  picture appears next to “@name” only if you turn on “Also show on my published pages” (default: off).
- **Federation:** people on other Saga instances show their picture too. Your server fetches it from their
  instance with a signed request (at most 512 KB), re-encodes it and keeps it for a day; disconnecting the
  instance deletes the cache.
- **Deleting an account** removes its picture, settings and all cached pictures.

### Language

Saga speaks English, German, Spanish, Russian (Русский) and Thai (ไทย); this guide is available in English, German
and Spanish. The language you see is decided in this order:

1. your own choice under **Settings** → **Account** → **Language** (or user menu → **Language**) – it applies
   immediately and in every browser, because it is stored in your account,
2. the **🌐 switcher on the sign-in page** – for someone else's computer that doesn't speak your language. It also
   applies to the 2FA step, registration and error pages; the browser only remembers the language (cookie
   `saga_lang`, no tracking), "Automatic (browser)" removes it again,
3. the app's language, when an app opens the sign-in in the browser,
4. the **default language** the admin sets under General,
5. your browser's language,
6. otherwise English (also for any other browser language).

"Automatic" always means: the next step decides. Dates and numbers follow the selected language. The admin area
has a switcher too (header and sign-in). In the apps you choose the language under Settings → Language (Automatic,
Deutsch, English, Español, Русский, ไทย). Russian and Thai are machine-translated – improvements from native
speakers are welcome.

### On your phone

Saga adapts to small screens and can be added to your home screen like an app ("Add to Home Screen" in the
browser menu). On pages, the content is right at the top; the page tree opens as a drawer from the button at the
top left. `/` opens the block menu as a sheet from the bottom – tap an entry, or keep typing (filters) and press
`Enter`. Tab bars (settings, admin area) can be swiped sideways.

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
The AI can use the same connection (“Allow web search” under AI assistant, see above).

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

The easiest way is to let Saga do it: **encrypted backups to S3, a folder or Nextcloud/WebDAV** (admin area → Backups,
see below).

Restore the backups that Saga creates itself (`/data/backups`, see Updates) with `python run.py restore` (section
“Going back to the previous version”), not with the `pg_restore` in the database container: they come from `pg_dump`
17 in the Saga image, and `pg_restore` 16 from `postgres:16` reports “unsupported version (1.16) in file header” for
them.

### Encrypted backups (S3, folder, Nextcloud/WebDAV)

Admin area → **Backups**: every day (time of your choice) Saga backs up the database, the files (`/data/files` without
thumbnails, `/data/chat`, `/data/svc-icons`) and, if you want, the keys from `.env` (`SECRET_KEY`, `CAL_SECRET_KEY`) –
encrypted – to a target: **S3** (MinIO, Garage, Backblaze B2, Hetzner Object Storage, Wasabi, AWS …), a **folder in
the container** (e.g. a mounted drive or a NAS share) or **WebDAV** (Nextcloud, ownCloud, Storage Box …). The local
backups before updates (`/data/backups`, `run.py backup`/`restore`) stay as they are. The server backup contains the
data of **all** people – the target should be meant for that (its own bucket or account), not someone's private cloud
storage.

1. **Connect a target** – choose the type:

   **S3.** Endpoint (`https://host[:port]`, without the bucket), region (empty = `us-east-1`; Garage: `garage`, Hetzner
   e.g. `fsn1`, Backblaze e.g. `eu-central-003`), bucket (must exist – Saga doesn't create one), prefix (folder in the
   bucket, default `saga-backups`), access key and secret key (both stored encrypted like the other admin secrets).
   Addressing “automatic” uses virtual-hosted style for AWS (`https://bucket.host/…`), otherwise path style
   (`https://host/bucket/…`, MinIO/Garage) – set it explicitly if needed. Saga signs every request itself (AWS
   Signature v4, no extra library) including the SHA-256 of the content; large files go up as a multipart upload (parts
   of `BACKUP_CHUNK_MB`, default 32 MB; aborted uploads are discarded). Recommendation: a key just for this bucket with
   read/write/delete/list; versioning or Object Lock additionally protect against a compromised server deleting the
   backups – then clean up old versions with a lifecycle rule of the bucket (Saga deletes according to the retention
   rule below). S3 doesn't report free space; the check before uploading is skipped.

   **Folder in the container.** Only below the roots in `SAGA_BACKUP_DIRS` (default `/backup`, several separated by
   commas); paths with `..` or symlinks pointing outside are rejected. Mount the folder as an additional volume and
   give the Saga user (UID 10001) write access:

   ```yaml
   # docker-compose.yml, service saga
       volumes:
         - saga-data:/data
         - ./certs:/certs:ro
         - /srv/saga-backups:/backup        # additional volume for backups (host folder or NAS mount)
   ```

   ```sh
   sudo mkdir -p /srv/saga-backups && sudo chown 10001 /srv/saga-backups
   docker compose up -d
   ```

   Then enter `/backup` (or `/backup/saga`) in the admin area. If the folder is on the same server, it only protects
   against errors in Saga or the database, not against losing the server – copy the folder somewhere else as well
   (restic, rsync …); the files are already encrypted.

   **WebDAV/Nextcloud.** For Nextcloud the address is enough (`https://cloud.example.org`): Saga detects Nextcloud,
   shows a sign-in link, you sign in to Nextcloud and allow access (Login Flow v2). Saga only stores the sign-in name
   and its own **app password** (encrypted); “Disconnect” revokes it in Nextcloud. Other WebDAV: address of the folder
   plus user and password. Recommendation: a **separate Nextcloud account** just for the backups (an app password may
   do everything the account may do) with a short trash retention and few versions – otherwise deleted old backups keep
   counting towards the storage in the trash. Internal addresses (homelab) work without any further approval.
2. **Key.** Encryption uses [age](https://age-encryption.org) and a **public** key. Either “Create key pair”: Saga
   shows the private key exactly once (text and download `saga-backup-<server>.key`) and does **not** store it – type
   the last 6 characters to confirm. Or enter your own public key (`age-keygen -o saga-backup.key` on your computer,
   take the line `# public key: age1…`; `ssh-ed25519 …` works too). Several recipients are possible (e.g. a second key
   in a safe). Without the private key no backup can be restored – not even by us.
3. **Schedule and retention.** “Back up automatically every day”, time (time zone `TIMEZONE`), retention
   “grandfather-father-son”: by default 7 daily, 4 weekly, 6 monthly (the newest run per day/week/month; runs from the
   last 24 hours are always kept). Before uploading Saga checks the free space, afterwards size and checksum (up to
   `BACKUP_VERIFY_MB` right away, larger files on Sundays). Large files go up in chunks on Nextcloud (Chunking v2) and
   as a multipart upload on S3. The run has low priority (`nice`/`ionice`); if several Saga processes use the same
   database, only one of them backs up.
4. **Errors.** Status in the admin area (last success, next run, history). If a run fails or there has been no success
   for 48 hours, all admins get a notification (bell and, if set up, on their phone); optionally also a webhook (ntfy
   topic, Gotify `…/message?token=…` or any address).

**What is stored at the target** – one folder (on S3: prefix) per run, `saga-YYYYMMDD-hhmmss/`:

| File | Content |
|---|---|
| `db.dump.age` | Database (`pg_dump --format=custom`, checked with `pg_restore --list` before encrypting) |
| `dateien.tar.age` | `files/` (without `thumbs`), `chat/`, `svc-icons/` |
| `geheimnisse.env.age` | `SECRET_KEY`, `CAL_SECRET_KEY` (can be turned off) |
| `manifest.json.age` | Checksums of the unencrypted content |
| `manifest.json` | unencrypted, without names/host names: Saga version, schema version, sizes and SHA-256 of the `.age` files, key fingerprints. Uploaded last – a folder without `manifest.json` is incomplete. |

**Restoring with Saga** (new server, stack set up as in section 2):

```sh
# 1. read the secrets and copy SECRET_KEY / CAL_SECRET_KEY into .env (file from the target, e.g. via the Nextcloud client)
age -d -i saga-backup.key geheimnisse.env.age
# 2. stop Saga, fetch the run, check, decrypt, restore database + files
docker compose stop saga
docker compose run --rm -v "$PWD/saga-backup.key:/key:ro" \
  -e SAGA_BACKUP_S3_ENDPOINT=https://s3.example.org -e SAGA_BACKUP_S3_BUCKET=saga-backups \
  -e SAGA_BACKUP_S3_ACCESS_KEY='…' -e SAGA_BACKUP_S3_SECRET_KEY='…' -e SAGA_BACKUP_S3_PREFIX=saga-backups \
  saga python run.py restore --schluessel /key saga-20261019-032000
docker compose up -d
```

For a fresh stack, the target comes from the environment:

| Target | Settings |
|---|---|
| S3 | `SAGA_BACKUP_S3_ENDPOINT`, `SAGA_BACKUP_S3_BUCKET`, `SAGA_BACKUP_S3_ACCESS_KEY`, `SAGA_BACKUP_S3_SECRET_KEY`, optionally `SAGA_BACKUP_S3_REGION`, `SAGA_BACKUP_S3_PREFIX`, `SAGA_BACKUP_S3_STYLE` (`auto`/`path`/`virtual`) |
| Folder | `SAGA_BACKUP_PATH=/backup` (volume mounted as above) – or give the run folder itself instead of a name |
| WebDAV/Nextcloud | `SAGA_BACKUP_URL` (WebDAV address of the backup folder, e.g. `https://cloud.example.org/remote.php/dav/files/saga-backup/Saga-Backups`), `SAGA_BACKUP_USER`, `SAGA_BACKUP_PASSWORD` |

Without a run name Saga takes the newest complete one; `run.py backup-holen --liste` lists them all. If the target is
already set up in the admin area (same database), the `SAGA_BACKUP_*` settings aren't needed. Instead of a name you can
also give a local folder with the downloaded run (put it under `/data/…`). Saga compares the checksums from
`manifest.json` before decrypting. The database is restored through the same `db.restore` as `run.py restore` (all or
nothing); the secrets are also written to `/data/backups/<run>-geheimnisse.env` (readable only by Saga), and Saga warns
if they differ from `.env`.

**Restoring without Saga** (only `age` and the PostgreSQL tools; `age` is available for Linux, macOS and Windows):

```sh
cd saga-20261019-032000
sha256sum db.dump.age dateien.tar.age      # compare with manifest.json
age -d -i saga-backup.key db.dump.age > db.dump
age -d -i saga-backup.key dateien.tar.age | tar -x -C /path/to/data
age -d -i saga-backup.key geheimnisse.env.age
pg_restore --list db.dump | head           # look at the content; restore e.g. with pg_restore -d ... --clean --if-exists
```

**Test restore.** Recommended once a month (the admin area reminds you):

```sh
docker compose run --rm -v "$PWD/saga-backup.key:/key:ro" saga python run.py backup-pruefen --schluessel /key
```

fetches the newest run, compares the checksums, decrypts everything, restores the database into a **throwaway
database** (deleted afterwards) and checks the file archive – the real database stays untouched.

**Careful, identity:** a restored backup *is* the instance – including its federation key. If you try a backup on a
second server, set `SAGA_RESTORE_TEST=1` in `.env` there: Saga then makes no outgoing connections to the services of
the real instance – no federation, no notifications, no backups (no cleanup at the target, no revoking of the
Nextcloud app password either), no version check, no calendar sync, no AI, no Immich, no web search, no avatars from
outside. Saga itself, sign-in (including LDAP/OIDC) and the service status on the dashboard keep working. Only
*reading* from the configured backup target on the command line stays allowed (`backup-holen`, `restore --schluessel`,
`backup-pruefen`): exclusively to its address and read-only (WebDAV GET/PROPFIND, S3 GET/list) – uploading, deleting
and cleaning up at the target stay blocked.

Working folder: during a run the parts (before encryption briefly also `db.dump` in plain text) are kept under
`/data/backups/extern-tmp/` – the run and `backup-pruefen` each in their own subfolder; Saga deletes leftovers of an
aborted run on the next start.

### Personal backup (per person)

Under **Settings → My backup** everyone backs up **their own** workspace – independently of the admin's server backup,
to **their own** target and with **their own** key.

1. **Target:** your own Nextcloud (just enter the address, then sign in to Nextcloud and allow access – Saga gets its
   own app password, “Disconnect” revokes it) or another WebDAV target (address of the folder, user, password). Only
   **https addresses on the internet** are allowed; servers on the internal network only if the admin allows them under
   Backups → Personal backups (protection of the internal network).
2. **Key:** as for the admin – “Create key pair” (the private key is shown exactly once and not stored, confirm with
   the last 6 characters) or enter your own public key (`age1…`, `ssh-ed25519 …`); up to 5 recipients.
3. **What is backed up:** only what belongs to you – your own pages and subpages, databases with their entries, your
   own files and images, templates, archive, trash, versions, your own comments on your own pages, tasks, bookmarks,
   profile and settings. Every category can be turned off; individual **page trees** (with all subpages and files that
   only appear there) can be excluded. **Never** included: pages and files of other people (not even “Shared with
   me”), share lists and chats (end-to-end encrypted, they belong to everyone involved and can't be restored without
   the device keys). **Comments by others** on your pages are **off** by default (they belong to the people who wrote
   them) and can be turned on. Names of other people (e.g. who wrote a version) are left out.
4. **Schedule:** off, weekly or daily (time, weekday), “Backups to keep” = how many of the newest stay at the target.
   “Back up now” starts right away. If a backup fails, a notification appears in the bell.

**Format** (open, readable without Saga): `saga-YYYYMMDD-hhmmss.zip.age` – a ZIP encrypted with age – and next to it
`….zip.age.sha256` (checksum, `sha256sum -c` works). Inside the ZIP: `manifest.json` (SHA-256 of every file),
`daten.json` (everything, complete, for restoring), `seiten/`, `vorlagen/`, `archiv/`, `papierkorb/` (Markdown as in
the export, with relative links), `dateien/`, `profil/` and `LIESMICH.txt`. Decrypting without Saga:

```bash
age -d -i saga-backup.key -o backup.zip saga-20261019-024000.zip.age
```

**Restore backup** (same tab): upload a file (`.zip.age` or an already decrypted `.zip`) or pick a backup from your own
target, paste the private key (only sent to the server for this one operation, not stored). Saga restores **only into
your own account**, as **new pages** – links between the pages, database relations, embedded databases, buttons and
images then point to the new pages and files; existing pages stay unchanged. Default: into a new page “Restored
<date>”; trash, tasks and bookmarks if you want. Comments and settings are in the backup for reference but aren't
restored. A Markdown export (page → Export) contains no `daten.json` and therefore can't be restored. Limits when
restoring: `daten.json` at most 64 MB (`USER_BACKUP_DATA_MB`), at most 20,000 files (`USER_BACKUP_FILES_MAX`), the
newest 50 versions per page. The uploaded file is deleted right after restoring – also after an error; only after a
wrong key an encrypted backup stays for another attempt (at most 30 minutes).

**For admins – privacy:** the **server backup** (above) backs up everything needed to run the server, to *your*
target. **Personal backups** only go to the target of the person concerned, encrypted with their key – you don't have
to put your users' private data into your cloud, and Saga never sees a private key. In the admin area under
**Backups → Personal backups**: turn on/off (default on; off = no runs, no connecting, no restoring), **size per
backup** (default 1024 MB, upper limit `USER_BACKUP_MAX_MB`, default 4096), **minimum interval** for “Back up now”,
**most frequent schedule** (daily/weekly) and **allowed internal hosts** (otherwise only public https addresses). With
`SAGA_RESTORE_TEST=1` Saga doesn't connect anywhere for personal backups either.

### Updates

Saga uses version numbers according to [Semantic Versioning](https://semver.org/): **1.2.3** =
major.minor.patch. What has changed is listed in the [changelog](../../CHANGELOG.md) (in German) and in the
[releases](https://github.com/yggdrasil-sh/saga/releases). You can see the installed version at the bottom of
the admin area; a notice also appears there when a new version is available (can be turned off under General).

`SAGA_VERSION` in `.env` decides which version runs:

| Value | Meaning |
|---|---|
| `2` (default) | always the latest 2.x – new features and fixes, nothing that needs manual work |
| `2.3` | only bug fixes for 2.3 |
| `2.3.0` | exactly this version |
| `1` | stays on the latest 1.x (1.2) – older installations still have this value in `.env` |
| `testing` (or `rc`) | **Release candidate:** the latest from the `testing` branch, before the release. May contain bugs – only for trying things out |

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
docker compose exec saga python run.py backup              # database only
docker compose exec saga python run.py backup --dateien    # plus the pages' images/files as tar.gz
docker compose exec saga ls -l /data/backups
```

The pages' images and files (`/data/files`) are **not** part of the database backup. `--dateien` (or `--files`)
also creates `saga-dateien-<time>.tar.gz` (without thumbnails, they are recreated); restore it with
`docker compose exec saga tar -xzf /data/backups/<file> -C /data`. If you back up the `saga-data` volume anyway
(see above), you don't need the switch.

**Release candidates (channel `testing`):** With `SAGA_VERSION=testing` (equivalent: `rc`) you get new features as
soon as they are ready – before the release. Every build is a release candidate of the next version, e.g.
`2.4.0-rc.7` (7 changes since the last release; shown at the bottom of the admin area as "2.4.0-rc.7 (1cc210f)" with
🧪 Release candidate, followed by the commit). A release candidate is always newer than the stable version before it
(2.3.0 < 2.4.0-rc.7 < 2.4.0): the new-version notice only appears once 2.4.0 is out. Older testing builds were called
`x.y.z-testing.<commit>`. Back to the stable version: `SAGA_VERSION=2`, then `docker compose pull && docker compose up -d`.
If the testing version has already changed the database, the stable version only starts once it contains that
change too (with the next release at the latest) – or you restore the backup from before the switch.

A new **major version** may need manual work – the changelog will say so, and you only get it once you set
`SAGA_VERSION` to the new major version.

**From 1.x to 2.0:** If your `.env` still says `SAGA_VERSION=1`, you stay on 1.2. For Saga 2.0 set
`SAGA_VERSION=2` and update as above – the database is migrated automatically (with a backup first). Then, if you
want them, set up the AI assistant, federation and Apple notifications (APNs) in the admin area (they are off) and
check your reverse proxy for the live channel (section 3). Everything else is in the [changelog](../../CHANGELOG.md).

**From 2.0 to 2.1:** With `SAGA_VERSION=2`, 2.1 arrives with the normal update. The database moves to schema
version 6 (backup first; afterwards no way back to 2.0.x except via that backup). New and active right away:
profile pictures with **Gravatar** (can be turned off under General → Profile pictures) and **calls** using the STUN
server `stun:stun.nextcloud.com:443` (General → Calls; TURN optional, see "Calls"). Passkeys need a host name in
`BASE_URL`, not an IP address; the 2FA policy is set to "nobody". **AI in chats** and the AI's **web search** stay
off until you turn them on under AI assistant (web search only with SearXNG, section 10).

**From 2.1 to 2.3:** there is no version 2.2 – with `SAGA_VERSION=2`, 2.3 arrives directly from 2.1 with the normal
update. What happens and what to watch out for:

1. **Back up first (recommended):** Saga creates a backup itself before changing the database; an extra one including
   the files doesn't hurt: `docker compose exec saga python run.py backup --dateien`. If you already have a target for
   encrypted backups, start “Back up now” there.
2. **Update** as above (`git pull`, `docker compose pull`, `docker compose up -d`). The database moves from schema
   version 6 to **9** (migrations 7–9 for federation; backup `…-vor-schema-9.dump` in `/data/backups` first). The log
   shows `saga:` lines with backup and migration. On the first start Saga indexes all pages once for search, backlinks
   and shares (about 1–2 s per 1,000 pages, in the background – Saga is usable meanwhile and results are correct from
   the start).
3. **Check federation:** connected servers on 2.3 get **chat, calls and address book** per server – on by default.
   Display names and profile pictures of your people are then sent to those servers. If you don't want that, turn it
   off under Admin → Federation → “Features per instance” (or only share certain groups) and point people to the
   opt-out under Settings → Account. Servers on 2.1 stay connected as before.
4. **Set up new features (optional):** encrypted backups under Admin → **Backups** (off until a target is set up; a
   folder target needs an extra volume, see “Encrypted backups”). **Personal backups** and **Nextcloud files in
   pages** are allowed, but only to public https addresses until you allow internal hosts (Backups → Personal backups
   and General → “User connections”; both can also be turned off there).
5. **New `.env` settings** – all optional, the defaults usually fit (section 13): `FED_*` (federation: upload limit,
   delivery, rates, cache for chat attachments), `SAGA_BACKUP_DIRS` (roots for the folder target) and `SAGA_BACKUP_*`
   (only for restoring on a fresh server), `BACKUP_VERIFY_MB`, `BACKUP_CHUNK_MB`, `USER_BACKUP_*` (personal backups),
   `NC_*` (Nextcloud files), `SAGA_RESTORE_TEST` (test restore), `GZIP_LEVEL`/`GZIP_MIN_BYTES`, `NOTE_ROW_MAX_COUNT`,
   `SAGA_ORJSON`.
6. **Apps:** apps 2.3 are recommended ([Saga apps](https://github.com/yggdrasil-sh/saga-apps/releases)) – with
   federated chats and calls, sharing across servers and “My backup”. Apps 2.1 keep working, just without these
   features; they show chats with people on other servers with “please update the app”.

**Way back 2.3 → 2.1:** only via the backup from before the update (`…-vor-schema-9.dump`) – see below. On the changed
database 2.1 reports “Die Datenbank hat Schema-Stand 9, diese Saga-Version kennt nur 6” and doesn't start.

**Going back to the previous version:** An older Saga will not start on a database that a newer version has
already changed (it reports this in the log instead of damaging data). This applies to **2.3 → 2.1** (backup
`…-vor-schema-9.dump`) and to **2.1 → 2.0.x**: 2.1
raises the schema version to 6 (backup `…-vor-schema-6.dump`), and 2.0.x then stops with "Die Datenbank hat
Schema-Stand 6, diese Saga-Version kennt nur 5" – otherwise 2.0 would keep running and let accounts with a second
factor in with just the password. In that case, restore the backup from before the update:

```sh
docker compose stop saga
docker compose run --rm saga ls /data/backups                     # e.g. saga-20261013-183000-vor-schema-3.dump
docker compose run --rm saga python run.py restore /data/backups/saga-20261013-183000-vor-schema-3.dump
# set SAGA_VERSION in .env to the old version (e.g. 1.2.3), then:
docker compose pull && docker compose up -d
```

Everything changed after the backup is lost – so only do this shortly after a failed update.

So restore **before** changing `SAGA_VERSION`, with the newer version (as above): it restores the backup in one
transaction (if something fails, everything stays as it was), works with `postgres:16` and `:17` and removes the
tables an older version doesn't know first. Older Saga versions fail to restore with `postgres:16` because of
“unrecognized configuration parameter transaction_timeout” and – on a database that 2.3 (or a 2.2.0-rc testing
version) has already used – because of the new tables `note_derived`, `note_link` and `note_stale` (“cannot drop table
note because other objects depend on it”). If you have already switched, use the newer version just for restoring,
e.g. `docker compose run --rm saga …` with `SAGA_VERSION=2` (or `testing`) in `.env`. You can also remove the three
tables by hand (2.3 recreates them on its next start):
`docker compose exec -T db psql -U saga -d saga -c "DROP TABLE IF EXISTS note_link, note_stale, note_derived"`.

**Apps:** The apps check the server version on start. If an app is too old for the server, it asks you to
update it. The app's settings show both the app and the server version.

## 12. Troubleshooting

**After signing in I end up on the sign-in page again.**
`BASE_URL` must be exactly the address shown in the browser (including `https://`). With `https`, Saga only
sets cookies over HTTPS.

**"Too many failed attempts".**
After 8 wrong passwords per account (30 per address) within 15 minutes, Saga makes you wait. Behind a reverse
proxy, set `TRUSTED_PROXIES` – otherwise all visitors share the proxy's address.

**Passkey: "The browser reports the address …, but Saga is set up for …".**
Saga was opened via an address other than `BASE_URL` (e.g. an IP address or an old name). Passkeys are bound to
the address – open Saga via `BASE_URL` or correct `BASE_URL`. "Passkeys need an address with a host name":
`BASE_URL` contains an IP address or no `https://`.

**Service icons are missing.**
Saga downloads them once from `cdn.jsdelivr.net` (selfh.st) and stores them. Without internet access, the first
letter is shown. With `ICON_CDN=` (empty), Saga fetches nothing from the internet at all.

**LDAP: service account sign-in failed, or certificate errors.**
Check with "Test connection". With your own CA, put the file into `certs/` (section 7).

**SSO: SSO sign-in failed.**
Check the redirect URI at your provider (`BASE_URL/auth/callback`) and verify the discovery document with
"Test connection". Details are in `docker compose logs saga`.

**Calendar: server not reachable or not allowed.**
Internal servers must be allowed under Admin → General. Calendars and Immich don't go through
`HTTPS_PROXY`/`HTTP_PROXY` (so the check can't be bypassed via the proxy) – Saga needs a direct connection to
these servers.

**"Too many sign-ins right now – please try again in a moment."**
Saga checks at most `KDF_PARALLEL` passwords at the same time (default 4, about 32 MB of memory each) and waits at
most 2 seconds for a free slot. If this happens in everyday use (many users, powerful server), raise
`KDF_PARALLEL` in `.env`.

**Call: "Direct connection not possible", or it rings but never connects.**
One side is behind a strict firewall or mobile CGNAT. Configure a TURN server (section "Calls" → "TURN over port 443").
If the call window shows "via relay (TURN)", TURN works. "Camera/microphone not allowed": allow it in the browser via
the lock icon next to the address; calls need `https`. If other pages don't ring, check that the live channel gets
through the proxy (section 3).

**Uploading or saving fails with "too large" (413).**
Files: `ATTACH_MAX_MB` (default 10). Everything else, e.g. very long pages: `REQUEST_MAX_MB` (default 2). If the
message comes from the reverse proxy, raise the limit there (section 3).

**After changing my password I have to sign in to the apps again.**
Intended: a new password signs out all other browser sessions and apps (e.g. in case a device was lost). Only the
session and the app where you changed the password stay signed in.

**The bell only updates after a minute, open pages don't update right away.**
Then the live channel isn't getting through and Saga falls back to polling every 60 seconds. Usually the reverse proxy
buffers the response of `/api/events` or closes it after a short time – settings in section 3. More than five open tabs
with the same account also switch the oldest ones to polling.

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
| `PUBLIC_HOSTS` | – | Custom domains for public pages only (comma-separated, section 9 “Publishing pages”) |
| `PUBLIC_RATE` | `120` | Requests to public pages per IP address and minute (images: five times as many) |
| `SESSION_HOURS` | `12` | Lifetime of a browser session (apps stay signed in) |
| `PASSKEY_RP_ID` | host name from `BASE_URL` | Domain of the passkeys; only set it if they should be valid for a parent domain (e.g. `example.org`) |
| `SAGA_VERSION` | `2` | Which version `docker compose pull` fetches: `2`, `2.3`, `2.3.0` or `testing` (section 11) |
| `BACKUP_KEEP` | `10` | How many automatic/manual backups are kept in `/data/backups` |
| `BACKUP_VERIFY_MB` | `512` | Encrypted backups (section 11): files up to this size are downloaded again completely after uploading and compared (larger ones on Sundays) |
| `BACKUP_CHUNK_MB` / `BACKUP_CHUNK_MIN_MB` | `32` / `10` | Part size for chunked uploads (Nextcloud Chunking v2, S3 multipart) and from which file size |
| `USER_BACKUP_MAX_MB` | `4096` | Personal backups (section 11): upper limit for the size per backup and for uploads when restoring (the limit in the admin area can only be smaller) |
| `USER_BACKUP_DATA_MB` / `USER_BACKUP_FILES_MAX` | `64` / `20000` | Restoring personal backups: size of `daten.json` and number of files per backup |
| `SAGA_BACKUP_DIRS` | `/backup` | Encrypted backups, target “Folder in the container”: only below these roots (comma-separated) |
| `SAGA_BACKUP_URL` / `SAGA_BACKUP_USER` / `SAGA_BACKUP_PASSWORD` | – | Only for `run.py backup-holen`/`restore --schluessel` on a fresh server: WebDAV address of the backup folder and credentials |
| `SAGA_BACKUP_S3_ENDPOINT` / `_BUCKET` / `_ACCESS_KEY` / `_SECRET_KEY` / `_REGION` / `_PREFIX` / `_STYLE` | – | The same for an S3 target (section 11, “Restoring with Saga”) |
| `SAGA_BACKUP_PATH` | – | The same for a folder target (e.g. `/backup`) |
| `SAGA_RESTORE_TEST` | – | `1` = test restore: no outgoing connections (federation, notifications, backups, version check, calendars, AI, Immich, web search, avatars); only reading from the backup target on the command line is allowed |
| `SAGA_UPDATE_CHECK` | `1` | `0` = never ask GitHub for new versions (can also be turned off in the admin area) |
| `ADMIN_SESSION_HOURS` | `4` | Lifetime of a session in the admin area |
| `TIMEZONE` | `Europe/Berlin` | Time zone for exports |
| `ATTACH_MAX_MB` / `ATTACH_QUOTA_MB` | `10` / `500` | Size per file / storage per user |
| `AVATAR_MAX_MB` | `5` | Largest file when uploading a profile picture |
| `CHAT_QUOTA_MB` | `4096` | Storage for chat attachments per user (encrypted, under `STATE_DIR/chat`; uploads in progress count). Adjustable in the admin area |
| `VIDEO_MAX_MB` | `500` | Largest video in chats (format `saga-file/v2`, resumable upload). Adjustable in the admin area |
| `SAGA_MEDIA` | `on` | `off` = start the container without saga-media (Saga directly on port 8080 as up to 2.3; section 3 “Media path”) |
| `MEDIA_INTERNAL_PORT` | `8079` | With saga-media: Saga's internal port (`127.0.0.1` only) |
| `MEDIA_AUTHZ_TTL` | `60` | saga-media: a read permission for an attachment is remembered for at most this many seconds (0–60) |
| `MEDIA_MAX_CONNS` / `MEDIA_MAX_STREAMS` / `MEDIA_MAX_STREAMS_IP` | `4096` / `1024` / `128` | saga-media: open connections / parallel file downloads in total / per client address |
| `MEDIA_STALL_TIMEOUT` | `60` | saga-media: seconds without progress on an upload or download before the connection is closed |
| `MEDIA_ACCEL` / `MEDIA_ACCEL_PREFIX` | `off` / `/_saga_media/` (Caddy `/`) | Without saga-media: `nginx` or `caddy` = the reverse proxy serves chat attachments via `X-Accel-Redirect` |
| `MEDIA_PY_STREAMS` | `4` | Without saga-media and `MEDIA_ACCEL`: Python serves at most this many attachments at a time |
| `REQUEST_MAX_MB` | `2` | Largest request apart from file uploads (e.g. very long pages); larger ones get 413 |
| `KDF_PARALLEL` | `4` | Password checks at the same time (about 32 MB of memory each); others wait up to 2 s |
| `NOTE_MAX_COUNT` | `5000` | Pages per user (not counting database entries and trash) |
| `NOTE_ROW_MAX_COUNT` | `50000` (at least 10 × `NOTE_MAX_COUNT`) | Database entries per user (not counting trash). Safety net: everything incl. trash at most 2 × (both limits) |
| `GZIP_LEVEL` / `GZIP_MIN_BYTES` | `5` / `1024` | Compression (gzip) for JSON, text, JS, CSS from this size; `0` = off. Never for the live channel, images and files |
| `SAGA_ORJSON` | `1` | `0` = JSON responses with the standard library instead of `orjson` (same output, just slower) |
| `IMMICH_URL` | – | Suggestion in the Immich dialog (allowed automatically) |
| `NEXTCLOUD_URL` | – | Suggestion in the “Nextcloud” tab of the settings (host allowed automatically) |
| `NC_CACHE_MB` | `300` | Nextcloud files in pages: limit for cached thumbnails (`/data/nc-cache`, oldest removed first) |
| `NC_DOWNLOAD_MAX_MB` | `2048` | Nextcloud files in pages: largest download through Saga (streamed) |
| `NC_FED_MAX_MB` | `50` | Nextcloud files in pages: largest download for people on other Saga instances |
| `ICON_CDN` | selfh.st via jsDelivr | Source of service icons; empty = none |
| `STATUS_INTERVAL` | `60` | Seconds between status checks |
| `CACHE_TTL` | `60` | Seconds that account and service data is cached |
| `LIVE_PING` | `25` | Seconds until the heartbeat line in the live channel (keeps proxies open) |
| `LIVE_MAX_PER_USER` / `LIVE_MAX_TOTAL` | `5` / `2000` | Live connections per account (the oldest is closed) / in total |
| `LIVE_MAX_AGE` | `3600` | After this many seconds the browser reopens the live connection (the session is checked again) |
| `FED_ALLOW_HTTP` | – | `1` = allow federation over http (tests only; otherwise https only) |
| `FED_RATE` / `FED_TIMEOUT` | `600` / `10` | Requests per minute and connected instance / seconds until timeout |
| `FED_UPLOAD_QUOTA_MB` | `200` | Storage per person of another server for uploads into pages here (also counts for the owner) |
| `FED_WATCH_TTL` | `90` | Seconds a subscription (live/presence) to a page of another server lasts without renewal |
| `FED_EXPRESS_TIMEOUT` / `FED_EXPRESS_WORKERS` | `5` / `8` | Seconds for immediate delivery (then the outbox) / threads for it |
| `FED_PEER_SLOTS` / `FED_OUT_WORKERS` | `3` / `4` | Background jobs per connected server at the same time (a slow server can't occupy more) / threads for the outbox |
| `FED_CHAT_IN_RATE` | `1200` | Chat messages per minute a server, as home, may deliver here (above that: later, again) |
| `FED_CHAT_CACHE_MB` / `FED_CHAT_CACHE_DAYS` | `1024` / `30` | Cached copies of chat attachments from other servers: storage per server / days without access until they expire (fetched again when needed; those from disconnected servers are kept) |
| `SEARXNG_URL` / `SEARXNG_SECRET` | – | Web search (section 10) |
| `AI_WEB_FETCH` / `AI_WEB_RATE` | `2` / `10` | AI web search: result pages whose text is read (0 = snippets only); web searches per person in 10 minutes |
| `SEARCH_LANGUAGE` / `SEARCH_SAFESEARCH` | `de-DE` / `0` | Language and filter of the web search |
| `LOG_LEVEL` | `info` | Log verbosity |
| `DATABASE_URL` | from `POSTGRES_*` | Custom database connection (instead of the `db` container) |
