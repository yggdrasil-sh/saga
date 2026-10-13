"""Umzug von Hliðskjálf (dem Vorgänger mit SQLite, Keycloak und FreeIPA) nach Saga.

Laeuft im Saga-Container (Daten-Volume und Datenbank verbunden), z. B.:

    docker compose run --rm -v /pfad/import:/import:ro -v ./tools:/tools:ro saga \\
        python /tools/migrate_hlidskjalf.py daten --sqlite /import/hlidskjalf.db --files /import/files --env /import/hlidskjalf.env

    python tools/migrate_hlidskjalf.py env /pfad/hlidskjalf.env > .env      # .env fuer Saga aus der alten erzeugen

daten:  kopiert Seiten, Versionen, Aufgaben, Dateien, Favoriten, Kalender-/Immich-Verbindungen, App-Schluessel und
        App-Anmeldungen; legt fuer jeden Benutzer ein Saga-Konto an (Herkunft SSO); mit --env zusaetzlich die
        Einstellungen fuer Keycloak (OIDC) und FreeIPA (LDAP: Gruppen, Dienste aus Gruppenbeschreibungen).
        Nur in eine leere Saga-Datenbank. Die Quelle wird nur gelesen.
env:    schreibt eine Saga-.env: gleiche Adresse, SECRET_KEY = altes SESSION_SECRET (damit gespeicherte Kalender- und
        Immich-Zugaenge lesbar bleiben), neues Datenbank-Passwort, Websuche wie bisher.
"""
import argparse
import os
import secrets
import shutil
import sqlite3
import sys
import time
from urllib.parse import urlparse

TABLES = ["task", "note", "note_version", "file", "fav", "app_token", "user_kv", "immich_key", "cal_account", "device_login"]


def read_env(path):
    out = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def cmd_env(a):
    o = read_env(a.old_env)
    if len(o.get("SESSION_SECRET", "")) < 32:
        sys.exit("SESSION_SECRET der alten .env fehlt oder ist kuerzer als 32 Zeichen")
    lines = [
        "# Saga - erzeugt aus der Hlidskjalf-.env (tools/migrate_hlidskjalf.py env)",
        f"BASE_URL={o['BASE_URL']}",
        "# gleicher Schluessel wie bisher: gespeicherte Kalender-/Immich-Zugaenge bleiben lesbar",
        f"SECRET_KEY={o['SESSION_SECRET']}",
        f"POSTGRES_PASSWORD={secrets.token_hex(24)}",
        "POSTGRES_USER=saga", "POSTGRES_DB=saga",
        f"HOST_BIND={o.get('HOST_BIND', '0.0.0.0')}", f"HOST_PORT={o.get('HOST_PORT', '8080')}",
        "ADMIN_BIND=127.0.0.1", "ADMIN_HOST_PORT=8081", "ADMIN_URL=http://127.0.0.1:8081",
        f"TRUSTED_PROXIES={o.get('TRUSTED_PROXIES', '127.0.0.1')}",
        f"CACHE_TTL={o.get('CACHE_TTL', '60')}", f"STATUS_INTERVAL={o.get('STATUS_INTERVAL', '60')}",
    ]
    for k in ("IMMICH_URL", "COMPOSE_PROFILES", "SEARXNG_URL", "SEARXNG_SECRET", "SEARCH_LANGUAGE", "SEARCH_SAFESEARCH",
              "TIMEZONE", "CANONICAL_REDIRECT", "ATTACH_MAX_MB", "ATTACH_QUOTA_MB", "CAL_SECRET_KEY"):
        if o.get(k):
            lines.append(f"{k}={o[k]}")
    print("\n".join(lines))


def cmd_data(a):
    sys.path.insert(0, "/app")
    import psycopg
    import auth
    import db
    db.wait_ready(60)
    db.init_schema()
    src = sqlite3.connect(f"file:{a.sqlite}?mode=ro&immutable=1", uri=True)   # nur lesen, auch von schreibgeschuetztem Laufwerk
    have = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    with psycopg.connect(db.DATABASE_URL) as pg:
        n = pg.execute("SELECT (SELECT COUNT(*) FROM note) + (SELECT COUNT(*) FROM task)").fetchone()[0]
        if n and not a.force:
            sys.exit(f"Die Saga-Datenbank enthaelt schon {n} Seiten/Aufgaben - Abbruch (nur in eine leere Datenbank).")
        uids = set()
        for t in TABLES:
            if t not in have:
                continue
            s_cols = [r[1] for r in src.execute(f"PRAGMA table_info({t})")]
            p_cols = {r[0] for r in pg.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s", (t,))}
            cols = [c for c in s_cols if c in p_cols]
            rows = src.execute(f"SELECT {', '.join(cols)} FROM {t}").fetchall()
            if rows:
                with pg.cursor() as cur:
                    cur.executemany(f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) ON CONFLICT DO NOTHING", rows)
                if "uid" in cols:
                    uids |= {r[cols.index("uid")] for r in rows}
            if "id" in p_cols and t in db.ID_TABLES:
                pg.execute(f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), GREATEST(COALESCE((SELECT MAX(id) FROM {t}), 0), 1))")
            print(f"  {t}: {len(rows)}")
        names = {}
        if "device_login" in have:
            names = {r[0]: r[1] for r in src.execute("SELECT uid, uname FROM device_login WHERE uname IS NOT NULL")}
        pg.commit()
    for uid in sorted(u for u in uids if u):
        if not auth.get_user(uid):
            with db.connect() as c, c:
                c.execute("INSERT INTO users (username, display_name, source, created) VALUES (?,?,?,?)",
                          (uid.lower(), names.get(uid, "") if names.get(uid, "") != uid else "", a.source, time.time()))
        print(f"  Konto {uid} ({a.source})")
    if a.files and os.path.isdir(a.files):
        dst = os.path.join(os.environ.get("STATE_DIR", "/data"), "files")
        os.makedirs(dst, exist_ok=True)
        shutil.copytree(a.files, dst, dirs_exist_ok=True)
        print(f"  Dateien: {sum(len(f) for _, _, f in os.walk(a.files))} nach {dst}")
    if a.env:
        settings_from_env(read_env(a.env), uids, auth)
    check_secrets()


def settings_from_env(o, uids, auth):
    base = o.get("LDAP_BASE", "")
    auth.save_setting("title", o.get("DASHBOARD_TITLE") or "Saga")
    auth.save_setting("group_prefix", o.get("GROUP_PREFIX", "s_"))
    auth.save_setting("registration", False)
    auth.save_setting("local_login", False)          # wie bisher: nur SSO (Admins kommen weiter in den Admin-Bereich)
    if o.get("OIDC_ISSUER"):
        auth.save_setting("oidc", {"enabled": True, "issuer": o["OIDC_ISSUER"].rstrip("/"), "client_id": o.get("OIDC_CLIENT_ID", ""),
                                   "client_secret": o.get("OIDC_CLIENT_SECRET", ""), "scopes": "openid profile email",
                                   "username_claim": "preferred_username", "name_claim": "name", "email_claim": "email",
                                   "groups_claim": "groups", "label": "Mit SSO anmelden", "auto_create": True,
                                   "allowed_group": "", "admin_group": "", "verify_tls": True})
        print("  Einstellungen: SSO (OIDC)", o["OIDC_ISSUER"])
    if o.get("LDAP_URI"):
        lc = dict(auth.DEFAULTS["ldap"])
        lc.update(auth.LDAP_PRESETS["freeipa"])
        lc.update({"enabled": True, "preset": "freeipa", "url": o["LDAP_URI"], "starttls": not o["LDAP_URI"].startswith("ldaps://"),
                   "verify_tls": True, "bind_dn": o.get("LDAP_BIND_DN", ""), "bind_password": o.get("LDAP_BIND_PW", ""),
                   "user_base": f"cn=users,cn=accounts,{base}", "group_base": f"cn=groups,cn=accounts,{base}",
                   "services_from_description": True, "password_login": False, "sso_groups": True, "auto_create": True})
        auth.save_setting("ldap", lc)
        print("  Einstellungen: LDAP", o["LDAP_URI"], "(Gruppen fuer SSO-Konten, Dienste aus Gruppenbeschreibungen)")
    hosts = set()
    for u in (o.get("IMMICH_URL", ""), o.get("CALDAV_ALLOWED_HOSTS", "")):
        for x in u.split(","):
            x = x.strip()
            if x:
                p = urlparse(x if "://" in x else "https://" + x)
                hosts.add(p.netloc.lower())
    import db
    with db.connect() as c:
        for (b,) in c.execute("SELECT DISTINCT base_url FROM cal_account").fetchall() + c.execute("SELECT DISTINCT base_url FROM immich_key WHERE base_url IS NOT NULL").fetchall():
            if b:
                hosts.add(urlparse(b).netloc.lower())
    auth.save_setting("allowed_hosts", ", ".join(sorted(hosts)))
    print("  Freigegebene Hosts (Kalender/Immich):", ", ".join(sorted(hosts)) or "-")
    if o.get("LDAP_URI"):
        for uid in sorted(uids):
            u = auth.get_user(uid)
            try:
                ok = auth.ldap_refresh(u)
                print(f"  Gruppen {uid}: {len(auth.groups_of(u['id'])[1])}" if ok else f"  WARNUNG: {uid} im Verzeichnis nicht gefunden/gesperrt")
            except Exception as ex:  # noqa: BLE001
                print(f"  WARNUNG: Gruppen fuer {uid} nicht gelesen: {type(ex).__name__}: {ex}")


def check_secrets():
    """Gespeicherte Kalender-/Immich-Zugaenge mit dem aktuellen SECRET_KEY lesbar?"""
    import app
    import db
    bad = 0
    with db.connect() as c:
        rows = c.execute("SELECT secret FROM cal_account").fetchall() + c.execute("SELECT secret FROM immich_key").fetchall()
    for (s,) in rows:
        try:
            app._cal_fernet().decrypt(s.encode())
        except Exception:  # noqa: BLE001
            bad += 1
    print(f"  Verschluesselte Zugaenge: {len(rows) - bad}/{len(rows)} lesbar"
          + ("" if not bad else "  -> SECRET_KEY muss dem alten SESSION_SECRET entsprechen!"))


def main():
    p = argparse.ArgumentParser(description="Umzug von Hlidskjalf nach Saga")
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("env")
    e.add_argument("old_env")
    d = sub.add_parser("daten")
    d.add_argument("--sqlite", required=True)
    d.add_argument("--files")
    d.add_argument("--env")
    d.add_argument("--source", default="oidc", choices=("oidc", "ldap", "local"))
    d.add_argument("--force", action="store_true")
    a = p.parse_args()
    cmd_env(a) if a.cmd == "env" else cmd_data(a)


if __name__ == "__main__":
    main()
