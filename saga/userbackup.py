"""Persoenliche Backups je Konto (2.3, "A3"): jede Person sichert IHREN Workspace in IHR EIGENES Ziel.

Warum getrennt vom Server-Backup (saga/backups.py): Das Server-Backup sichert alles (fuer den Betrieb, beim Admin). Hier
entscheidet die Person selbst, wohin (eigene Nextcloud oder ein anderes WebDAV-Ziel) und was - die Instanz muss keine
privaten Daten in ein fremdes Ziel tragen (DSGVO).

- Ziel: Ziel-Schnittstelle von saga/backups.py (Dav, Login Flow v2, Chunking v2). Strenger als beim Admin: nur https und
  nur oeffentliche Adressen (netguard prueft die IP der tatsaechlichen Verbindung); interne Hosts nur, wenn der Admin sie
  unter "Persoenliche Backups" freigibt. App-Passwort/Passwort verschluesselt (Fernet, eigene Ableitung), "Trennen"
  widerruft das App-Passwort in Nextcloud.
- Verschluesselung mit age und den oeffentlichen Schluesseln der Person (Saga kennt keinen privaten Schluessel; ein neu
  erzeugter privater Schluessel wird genau einmal angezeigt und nicht gespeichert).
- Inhalt NUR, was der Person gehoert: eigene Seiten/Unterseiten/Datenbanken samt Zeilen, eigene Dateien, Vorlagen, Archiv,
  Papierkorb, Versionen, eigene Kommentare auf eigenen Seiten, Aufgaben, Lesezeichen, Einstellungen/Profil. Nie: "Mit mir
  geteilt", Seiten/Dateien anderer Konten, Freigabe-Listen. Kommentare anderer auf eigenen Seiten nur, wenn eingeschaltet
  (Standard aus). Fremde Benutzernamen (angelegt/geaendert von, Versions-Autor) werden weggelassen.
  Chats NICHT: Ende-zu-Ende-verschluesselt mit Geraeteschluesseln, gehoeren allen Beteiligten, nicht wieder einspielbar.
- Format (offen, ohne Saga lesbar): saga-<JJJJMMTT-hhmmss>.zip.age = age-verschluesseltes ZIP mit manifest.json
  (SHA-256 jeder Datei), daten.json (vollstaendig, fuer den Import), seiten/ vorlagen/ archiv/ papierkorb/ (Markdown wie
  der Export), dateien/, profil/, LIESMICH.txt. Daneben saga-<...>.zip.age.sha256 (sha256sum-Format; erst damit gilt ein
  Backup als vollstaendig).
- Wiederherstellen: ins EIGENE Konto als neue Seiten (neue IDs; Verweise, Datenbank-Relationen, eingebettete Datenbanken,
  Schaltflaechen und Bilder umgeschrieben), optional in eine Seite "Wiederhergestellt <Datum>". Der private Schluessel
  geht nur fuer diesen Vorgang an den Server (age liest ihn von stdin) und wird nirgends gespeichert.
- Admin: Schalter (Standard an), Groesse, Haeufigkeit, freigegebene interne Hosts (auth "user_backup").
"""
import binascii
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone
from urllib.parse import urlparse

import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

import auth
import db
from db import connect as _db
from saga import backups, chat, contacts, i18n, netguard, nextcloud, notify
from saga.backups import BackupError
from saga.core import BASE_URL, E, RESTORE_TEST, SECRET, TZ, _api_guard, _bad, _body, _json_load, account_lang, t
from saga.notes import FILES_DIR, FILE_QUOTA, NOTE_BODY_MAX, NOTE_TOTAL_MAX, VERSION_MAX, _FILE_URL, _clean_props, _clean_schema, tree_changed
from version import VERSION

router = APIRouter()

FORMAT = "saga-userbackup/1"
KIND = "userbackup.failed"
LOCK_ID = 424251                                   # pg_advisory_lock: Zeitplan (einer je Instanz) / je Konto (zweiter Wert)
NAME_RE = re.compile(r"^saga-(\d{8})-(\d{6})\.zip\.age$")
TIME_RE = backups.TIME_RE
HARD_MAX_MB = max(1, int(E.get("USER_BACKUP_MAX_MB", "4096")))     # obere Grenze fuer Uploads (BodyLimit in app.py)
HISTORY_KEEP = 30
UPLOAD_TTL = 1800                                  # hochgeladenes Backup (zum Einspielen) bleibt so lange liegen
# Grenzen beim Einspielen (unabhaengig von der Backup-Groesse): daten.json wird ganz gelesen (JSON) - daher eigene Grenze,
# beim Lesen abgebrochen; Dateien und Eintraege im ZIP gezaehlt, bevor etwas geschrieben wird; Versionen je Seite wie im
# Verlauf (VERSION_MAX, die neuesten).
DATA_MAX = max(1, int(E.get("USER_BACKUP_DATA_MB", "64"))) * 1024 * 1024
IMPORT_FILES_MAX = max(1, int(E.get("USER_BACKUP_FILES_MAX", "20000")))
KEY_TTL = 900
MB = backups.MB
KV_KEYS = ("lang", "notify_off", "push", "ai_history", "fed_directory", "chat_receipts", "chat_online",
           "task_due")       # eigene Einstellungen (keine Sicherheitsdaten; 2.4: Gelesen/Online; 2.5: taegliche Zusammenfassung)
CATS = ("pages", "templates", "archive", "trash", "versions", "files", "comments", "comments_others", "tasks", "bookmarks", "settings")
CAT_DEFAULT = {k: k != "comments_others" for k in CATS}
SCHEDULES = ("off", "weekly", "daily")
ADMIN_DEFAULT = {"enabled": True, "max_mb": 1024, "min_h": 1, "max_schedule": "daily", "hosts": ""}
DEFAULT = {"mode": "", "server": "", "url": "", "user": "", "dav_user": "", "flow": False, "folder": "Saga-Backup",
           "recipients": [], "schedule": "off", "time": "02:40", "weekday": 6, "keep": 7, "cats": dict(CAT_DEFAULT),
           "exclude": [], "since": 0}


def tmp_root():
    return os.path.join(db.BACKUP_DIR, "user-tmp")


# =====================================================================================================
# Admin-Einstellungen, Ziel-Regeln (SSRF)
# =====================================================================================================
def admin_cfg():
    v = dict(ADMIN_DEFAULT)
    v.update(auth.settings().get("user_backup") or {})
    try:
        v["max_mb"] = max(1, min(HARD_MAX_MB, int(v.get("max_mb") or ADMIN_DEFAULT["max_mb"])))
        v["min_h"] = max(0, min(168, int(v.get("min_h") or 0)))
    except (TypeError, ValueError):
        v["max_mb"], v["min_h"] = ADMIN_DEFAULT["max_mb"], ADMIN_DEFAULT["min_h"]
    if v.get("max_schedule") not in ("daily", "weekly"):
        v["max_schedule"] = "daily"
    return v


def allowed_hosts():
    return {h.strip().lower() for h in re.split(r"[,\s]+", admin_cfg().get("hosts") or "") if h.strip()}


def _host_free(host, port=None):
    hs = allowed_hosts()
    host = str(host or "").lower().strip("[]")
    return host in hs or (port is not None and f"{host}:{port}" in hs)


def ip_ok(ip, host, port):
    """Verbindung erlaubt: oeffentliche Adresse - oder ein vom Admin freigegebener (interner) Host."""
    return netguard.is_public(ip) or _host_free(host, port)


def norm_target_url(url):
    """Eingabe -> (adresse, fehler). https, ohne Zugangsdaten/Abfrage; http nur fuer freigegebene Hosts."""
    u = backups.norm_url(url)
    if not u or len(u) > 500:
        return None, t("Adresse ungültig (http:// oder https://, ohne Zugangsdaten in der Adresse).")
    pu = urlparse(u)
    if pu.scheme != "https" and not _host_free(pu.hostname, pu.port or 80):
        return None, t("Nur https-Adressen erlaubt (http nur für vom Admin freigegebene interne Server)")
    return u, None


def _target_ok(cfg):
    """Gespeichertes Ziel noch erlaubt (Admin kann Freigaben zuruecknehmen)?"""
    u = cfg.get("server") if cfg.get("mode") == "nextcloud" else cfg.get("url")
    return bool(u) and norm_target_url(u)[0] is not None


# =====================================================================================================
# Speicher je Konto
# =====================================================================================================
def _fernet():
    import base64
    from cryptography.fernet import Fernet
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("hk-userbackup|" + SECRET).encode()).digest()))


def _enc(v):
    return _fernet().encrypt(v.encode()).decode() if v else ""


def _dec(v):
    try:
        return _fernet().decrypt(v.encode()).decode() if v else ""
    except Exception:  # noqa: BLE001 - Schluessel gewechselt: wie nicht gesetzt
        return ""


def load(uid):
    """-> (cfg, password, state)"""
    with _db() as c:
        r = c.execute("SELECT cfg, secret, state FROM user_backup WHERE uid=?", (uid,)).fetchone()
    cfg = dict(DEFAULT, cats=dict(CAT_DEFAULT))
    if r:
        got = _json_load(r[0], {})
        if isinstance(got, dict):
            cfg.update({k: v for k, v in got.items() if k in DEFAULT})
            cats = dict(CAT_DEFAULT)
            cats.update({k: bool(v) for k, v in (got.get("cats") or {}).items() if k in CATS})
            cfg["cats"] = cats
    st = _json_load(r[2], {}) if r else {}
    return cfg, (_dec(r[1]) if r else ""), (st if isinstance(st, dict) else {})


KEEP = object()


def save(uid, cfg, password=KEEP):
    v = {k: cfg.get(k, DEFAULT[k]) for k in DEFAULT}
    with _db() as c, c:
        if password is KEEP:
            r = c.execute("SELECT secret FROM user_backup WHERE uid=?", (uid,)).fetchone()
            sec = r[0] if r else ""
        else:
            sec = _enc(password or "")
        c.execute("INSERT INTO user_backup (uid, cfg, secret, updated) VALUES (?,?,?,?) ON CONFLICT (uid) DO UPDATE "
                  "SET cfg=EXCLUDED.cfg, secret=EXCLUDED.secret, updated=EXCLUDED.updated",
                  (uid, json.dumps(v, ensure_ascii=False), sec, time.time()))


def set_state(uid, **kw):
    with _db() as c, c:
        r = c.execute("SELECT state FROM user_backup WHERE uid=?", (uid,)).fetchone()
        cur = _json_load(r[0], {}) if r else {}
        cur = cur if isinstance(cur, dict) else {}
        cur.update(kw)
        c.execute("INSERT INTO user_backup (uid, cfg, state, updated) VALUES (?, '{}', ?, ?) ON CONFLICT (uid) DO UPDATE "
                  "SET state=EXCLUDED.state", (uid, json.dumps(cur), time.time()))


def has_target(cfg):
    return backups.has_target(cfg)


def dav_for(cfg, password):
    return backups.Dav(dict(cfg, password=password), ip_ok=ip_ok)


def delete_account(c, uid):
    """Kontoloeschung (saga/userdata.py): Einstellungen und Verlauf weg. Das App-Passwort widerruft revoke_on_delete()."""
    n = c.execute("DELETE FROM user_backup_run WHERE uid=?", (uid,)).rowcount
    return n + c.execute("DELETE FROM user_backup WHERE uid=?", (uid,)).rowcount


def revoke_on_delete(uid):
    """Vor der Kontoloeschung: ein per Login Flow erhaltenes App-Passwort in Nextcloud widerrufen (bestmoeglich)."""
    if RESTORE_TEST:
        return
    try:
        cfg, pw, _ = load(uid)
        if cfg.get("flow") and pw and cfg.get("server"):
            backups.nc_revoke(cfg["server"], cfg.get("user") or "", pw, ip_ok=ip_ok)
    except Exception:  # noqa: BLE001 - Loeschen geht immer vor
        pass


# =====================================================================================================
# Meldungen
# =====================================================================================================
def text(code, p=None):
    p = dict(p or {})
    if code == "disabled":
        return t("Persönliche Backups sind auf diesem Server abgeschaltet.")
    if code == "toolarge":
        return t("Backup zu groß: {size} (erlaubt: {max}). Weniger auswählen oder Dateien ausschließen.",
                 size=backups.size_text(p.get("size")), max=backups.size_text(p.get("max")))
    if code == "notallowed":
        return t("Das Ziel ist nicht (mehr) erlaubt – bitte neu verbinden.")
    if code == "failed":
        return t("Persönliches Backup fehlgeschlagen: {detail}", detail=text(p.get("code", "other"), p.get("p")))
    if code == "step.collect":
        return t("Inhalte zusammenstellen")
    if code == "step.encrypt":
        return t("Verschlüsseln")
    return backups.text(code, p)


def text_json(raw):
    d = _json_load(raw, {})
    return text(d.get("code", ""), d.get("p")) if isinstance(d, dict) and d.get("code") else ""


def _notif(c, uid, item, d):
    item["access"] = True
    item["system"] = True
    item["text"] = text(d.get("code", ""), d.get("p"))
    item["title"] = t("Mein Backup")
    item["url"] = "/?settings=backup"


notify.RENDER[KIND] = _notif


# =====================================================================================================
# Inhalt zusammenstellen (nur eigene Daten)
# =====================================================================================================
NOTE_COLS = ("id", "parent_id", "title", "icon", "body", "format", "kind", "schema", "props", "tags", "cover", "cover_pos", "wide", "font", "small",
             "sort", "pinned", "is_template", "archived", "deleted_at", "locked", "created", "updated", "created_by", "updated_by")


def _subtree(children, roots):
    out, stack = set(), list(roots)
    while stack:
        i = stack.pop()
        if i in out:
            continue
        out.add(i)
        stack.extend(children.get(i, ()))
    return out


def select_notes(c, uid, cats, exclude):
    """-> (ausgewaehlte Seiten {id: dict}, ausgelassene IDs). Nur Seiten des Kontos; Unterseiten folgen ihrer Elternseite."""
    rows = {r[0]: dict(zip(NOTE_COLS, r)) for r in c.execute(f"SELECT {', '.join(NOTE_COLS)} FROM note WHERE uid=?", (uid,))}
    if not cats.get("pages", True):
        return {}, set(rows)
    children = {}
    for r in rows.values():
        if r["parent_id"] in rows:
            children.setdefault(r["parent_id"], []).append(r["id"])
    drop = set()
    roots = [int(x) for x in exclude if isinstance(x, int) or str(x).isdigit()]
    drop |= _subtree(children, [x for x in roots if x in rows])
    rules = (("templates", lambda r: r["is_template"]), ("archive", lambda r: r["archived"]), ("trash", lambda r: r["deleted_at"]))
    for cat, hit in rules:
        if not cats.get(cat, True):
            drop |= _subtree(children, [i for i, r in rows.items() if hit(r)])
    return {i: r for i, r in rows.items() if i not in drop}, drop


def _refs(*texts):
    out = set()
    for s in texts:
        if s:
            out.update(_FILE_URL.findall(str(s)))
    return out


def _safe(s, n=80):
    s = re.sub(r"[^\w äöüÄÖÜß.\-]+", "_", s or "").strip(" ._") or t("Unbenannt")
    return s[:n]


def _people_out(schema_json, props_json):
    """Personen-Werte (Typ person) einer Zeile fuers Backup: lokale Konten als Handle (saga.people.to_ref), fremde als
    Prinzipal - Wiederherstellen auf diesem Server findet sie wieder, auf einem anderen fallen sie weg."""
    from saga.people import person_pids, to_ref
    pv = _json_load(props_json, {})
    pids = person_pids(_json_load(schema_json, {}))
    if not isinstance(pv, dict) or not pids:
        return props_json
    for pid in pids:
        if isinstance(pv.get(pid), list):
            pv[pid] = [to_ref(None, x) for x in pv[pid] if isinstance(x, str)]
    return json.dumps(pv, ensure_ascii=False)


def _people_in(c, uid, schema, raw):
    """Wiederherstellen: Personen-Werte (Handles/Prinzipale) -> gespeicherte Kennungen; Unbekannte fallen weg."""
    from saga.people import person_pids, to_canon
    for pid in person_pids(schema):
        v = raw.get(pid)
        if isinstance(v, list):
            raw[pid] = [u for u in (to_canon(c, x) for x in v if isinstance(x, str)) if u and (u.startswith("@") or
                        c.execute("SELECT 1 FROM users WHERE username=? AND disabled=0", (u,)).fetchone())]
    return raw


def _files_in(schema, raw, fids, own):
    """Wiederherstellen: Datei-Werte auf die neu eingespielten Dateien umschreiben; fremde/fehlende fallen weg."""
    for p in (schema or {}).get("props") or []:
        if p.get("type") != "files" or not isinstance(raw.get(p["id"]), list):
            continue
        out = []
        for x in raw[p["id"]]:
            m = _FILE_URL.fullmatch(str((x or {}).get("url") or "")) if isinstance(x, dict) else None
            nf = fids.get(m.group(1)) if m else None
            if nf and own(nf):
                out.append({"url": f"/files/{nf}", "name": str(x.get("name") or "")[:120]})
        raw[p["id"]] = out
    return raw


def collect(c, uid, cfg):
    """Alles, was ins Backup gehoert -> (daten, dateien {fid: zeile}, seiten {id: dict})."""
    cats, exclude = cfg["cats"], cfg.get("exclude") or []
    notes, dropped = select_notes(c, uid, cats, exclude)
    ids = sorted(notes)
    versions, comments = [], []
    if ids and cats.get("versions", True):
        for k in range(0, len(ids), 500):
            part = ids[k:k + 500]
            for r in c.execute(f"SELECT note_id, title, body, format, created, author FROM note_version WHERE uid=? AND note_id IN "
                               f"({','.join('?' * len(part))}) ORDER BY id", (uid, *part)):
                versions.append({"note_id": r[0], "title": r[1], "body": nextcloud.strip_authors(r[2], r[3], uid), "format": r[3], "created": r[4],
                                 "author": r[5] if r[5] == uid else None})
    if ids and cats.get("comments", True):
        others = cats.get("comments_others", False)
        for k in range(0, len(ids), 500):
            part = ids[k:k + 500]
            for r in c.execute(f"SELECT id, note_id, block_id, thread_id, uid, body, created, edited, resolved_at FROM note_comment "
                               f"WHERE owner_uid=? AND note_id IN ({','.join('?' * len(part))}) ORDER BY id", (uid, *part)):
                if r[4] != uid and not others:
                    continue                          # Kommentare anderer: Datenschutz der Kommentierenden (Standard aus)
                comments.append({"id": r[0], "note_id": r[1], "block_id": r[2], "thread_id": r[3], "author": r[4], "own": r[4] == uid,
                                 "body": r[5], "created": r[6], "edited": r[7], "resolved": bool(r[8])})
    files = {}
    if cats.get("files", True):
        used = _refs(*(x for n in notes.values() for x in (n["body"], n["icon"], n["cover"], n["props"])), *(v["body"] for v in versions))
        drop_ids, only_dropped = sorted(dropped), set()
        for k in range(0, len(drop_ids), 500):
            part = drop_ids[k:k + 500]
            for r in c.execute(f"SELECT body, icon, cover, props FROM note WHERE uid=? AND id IN ({','.join('?' * len(part))})", (uid, *part)):
                only_dropped |= _refs(*r)
        only_dropped -= used
        for r in c.execute("SELECT id, name, mime, size, is_image, created FROM file WHERE uid=? ORDER BY created", (uid,)):
            if r[0] in only_dropped:
                continue                              # gehoert nur zu ausgeschlossenen Seiten
            files[r[0]] = {"id": r[0], "name": r[1], "mime": r[2], "size": r[3], "is_image": bool(r[4]), "created": r[5]}
    tasks = [dict(zip(("id", "text", "done", "due", "created", "done_at", "prio", "repeat", "rep_anchor"), r)) for r in
             c.execute("SELECT id, text, done, due, created, done_at, prio, repeat, rep_anchor FROM task WHERE uid=? ORDER BY id", (uid,))] \
        if cats.get("tasks", True) else []
    # 2.5: Aufgabenquellen (Abos) - eigene Datenbanken werden beim Einspielen umgeschrieben, fremde nur mit Zugriff uebernommen
    task_sources = [{"db": r[0], "scope": r[1], "own": bool(r[2])} for r in
                    c.execute("SELECT s.db_id, s.scope, n.id FROM task_source s LEFT JOIN note n ON n.id=s.db_id AND n.uid=s.uid "
                              "WHERE s.uid=? ORDER BY s.added, s.db_id", (uid,)).fetchall()] \
        if cats.get("tasks", True) else []
    bookmarks = [dict(zip(("name", "url", "icon", "category", "sort", "created"), r)) for r in
                 c.execute("SELECT name, url, icon, category, sort, created FROM bookmark WHERE uid=? ORDER BY sort, id", (uid,))] \
        if cats.get("bookmarks", True) else []
    account, settings = {"username": uid}, {}
    if cats.get("settings", True):
        u = c.execute("SELECT username, display_name, email, source, created FROM users WHERE username=?", (uid,)).fetchone()
        if u:
            account = {"username": u[0], "display_name": u[1], "email": u[2], "source": u[3], "created": u[4]}
        pn = c.execute("SELECT name FROM public_name WHERE uid=?", (uid,)).fetchone()
        if pn:
            account["public_name"] = pn[0]
        for k, v in c.execute(f"SELECT k, v FROM user_kv WHERE uid=? AND k IN ({','.join('?' * len(KV_KEYS))})", (uid, *KV_KEYS)):
            settings[k] = v
    # 2.5: Kontakte im Chat (Kennung als Handle, nie Anmeldename; Chats selbst nicht - Ende-zu-Ende-verschluesselt)
    chat_contacts = contacts.export(c, uid) if cats.get("settings", True) else []
    out_notes = []
    for i in ids:
        n = dict(notes[i])
        n["parent_id"] = n["parent_id"] if n["parent_id"] in notes else None     # Elternseite nicht im Backup: oberste Ebene
        for k in ("created_by", "updated_by"):
            if n.get(k) and n[k] != uid:
                n[k] = None                           # keine fremden Benutzernamen
        n["body"] = nextcloud.strip_authors(n.get("body"), n.get("format"), uid)    # auch nicht in Nextcloud-Bloecken
        par = notes.get(n["parent_id"]) if n["parent_id"] else None
        if par and par.get("kind") == "database" and n.get("props") and '"person"' in (par.get("schema") or ""):
            n["props"] = _people_out(par.get("schema"), n["props"])           # 2.5: Personen als Handle, nie Anmeldenamen
        out_notes.append(n)
    data = {"format": FORMAT, "saga": VERSION, "created": time.time(), "account": account, "settings": settings,
            "options": {k: bool(cats.get(k, CAT_DEFAULT[k])) for k in CATS}, "excluded": len([x for x in exclude]),
            "notes": out_notes, "versions": versions, "comments": comments, "files": list(files.values()),
            "tasks": tasks, "task_sources": task_sources, "bookmarks": bookmarks, "chat_contacts": chat_contacts}
    return data, files, notes


def _avatar(c, uid):
    r = c.execute("SELECT mime, data FROM avatar_img WHERE uid=? AND src='upload' ORDER BY size DESC LIMIT 1", (uid,)).fetchone()
    return (r[0], bytes(r[1])) if r else None


class _HashZip:
    """ZIP schreiben und je Eintrag Groesse + SHA-256 fuer das Manifest merken."""

    def __init__(self, path, limit):
        self.z = zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, allowZip64=True)
        self.sums, self.total, self.limit = {}, 0, limit

    def _count(self, n):
        self.total += n
        if self.limit and self.total > self.limit:
            raise BackupError("toolarge", size=self.total, max=self.limit)

    def put(self, name, data):
        data = data.encode() if isinstance(data, str) else data
        self._count(len(data))
        self.z.writestr(name, data)
        self.sums[name] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    def put_file(self, name, path):
        size = os.path.getsize(path)
        self._count(size)
        h = hashlib.sha256()
        with open(path, "rb") as src, self.z.open(name, "w", force_zip64=size > 2 ** 31) as dst:
            for b in iter(lambda: src.read(MB), b""):
                h.update(b)
                dst.write(b)
        self.sums[name] = {"size": size, "sha256": h.hexdigest()}

    def close(self):
        self.z.close()


def _readme(uid, name, data):
    lines = [t("Saga – persönliches Backup"), "=" * 40, "",
             t("Konto: {user} · erstellt: {when} · Saga {v}", user=uid,
               when=datetime.fromtimestamp(data["created"], TZ).strftime("%Y-%m-%d %H:%M"), v=VERSION), "",
             t("Entschlüsseln (ohne Saga, mit age – https://age-encryption.org):"),
             f"  age -d -i saga-backup.key -o backup.zip {name}", "",
             t("Inhalt:"),
             "  manifest.json  – " + t("Prüfsummen (SHA-256) aller Dateien"),
             "  daten.json     – " + t("alles vollständig (zum Einspielen in Saga: Einstellungen → Mein Backup → Backup einspielen)"),
             "  seiten/ vorlagen/ archiv/ papierkorb/  – " + t("Seiten als Markdown"),
             "  dateien/       – " + t("eigene hochgeladene Dateien und Bilder"),
             "  profil/        – " + t("Profilbild"),,
             "  daten.json → chat_contacts – " + t("Chat-Kontakte (Name und Kennung, ohne Nachrichten)"), "",
             t("Nicht enthalten: Seiten und Dateien anderer Personen (auch nicht, was mit dir geteilt ist) und Chats (Ende-zu-Ende-verschlüsselt)."),
             ""]
    return "\n".join(lines)


def build_zip(uid, cfg, path, limit, name):
    """Backup-ZIP schreiben -> Zaehler. Inhalte nur aus dem Konto uid."""
    from saga.page_tools import md_page
    with _db() as c:
        data, files, notes = collect(c, uid, cfg)
        avatar = _avatar(c, uid) if cfg["cats"].get("settings", True) else None
    z = _HashZip(path, limit)
    try:
        # Markdown: Seitenbaum wie der Export; je Bereich (Seiten, Vorlagen, Archiv, Papierkorb) ein Ordner
        rows = {i: [i, n["title"], n["icon"], n["parent_id"] if n["parent_id"] in notes else None, n["body"], n["format"], n["kind"],
                    n["schema"], n["props"], n["created"], n["updated"]] for i, n in notes.items()}
        paths, used = {}, set()
        fpaths = {fid: f"dateien/{fid[:8]}_{_safe(f['name'], 100)}" for fid, f in files.items()}

        def area(n):
            return "papierkorb/" if n["deleted_at"] else "archiv/" if n["archived"] else "vorlagen/" if n["is_template"] else "seiten/"

        def place(i, folder):
            base, k = _safe(rows[i][1]), 1
            nm = base
            while f"{folder}{nm}.md".lower() in used:
                k += 1
                nm = f"{base} ({k})"
            used.add(f"{folder}{nm}.md".lower())
            paths[i] = f"{folder}{nm}.md"
            for ch in sorted((x for x in rows.values() if x[3] == i), key=lambda x: (x[1] or "", x[0])):
                place(ch[0], f"{folder}{nm}/")
        for i in sorted((i for i in rows if rows[i][3] is None), key=lambda i: (rows[i][1] or "", i)):
            place(i, area(notes[i]))
        for i, p in paths.items():
            def rel(h, here=p):
                m = re.fullmatch(r"/notes#(\d+)", h)
                if m and int(m.group(1)) in paths:
                    return os.path.relpath(paths[int(m.group(1))], os.path.dirname(here) or ".").replace(" ", "%20")
                m = _FILE_URL.fullmatch(h)
                if m and m.group(1) in fpaths:
                    return os.path.relpath(fpaths[m.group(1)], os.path.dirname(here) or ".").replace(" ", "%20")
                return (BASE_URL + h) if h.startswith(("/notes#", "/files/")) else h
            z.put(p, md_page(rows, rows[i], rel, uid))
        for fid, fp in fpaths.items():
            src = os.path.join(FILES_DIR, fid)
            if os.path.isfile(src):
                z.put_file(fp, src)
                files[fid]["path"] = fp
                files[fid]["sha256"] = z.sums[fp]["sha256"]
        if avatar:
            ext = {"image/png": "png", "image/webp": "webp", "image/jpeg": "jpg"}.get(avatar[0], "bin")
            z.put(f"profil/avatar.{ext}", avatar[1])
            data["account"]["avatar"] = f"profil/avatar.{ext}"
        data["files"] = list(files.values())
        z.put("daten.json", json.dumps(data, ensure_ascii=False, indent=1))
        z.put("LIESMICH.txt", _readme(uid, name, data))
        counts = {"pages": len(notes), "files": sum(1 for f in files.values() if f.get("path")), "versions": len(data["versions"]),
                  "comments": len(data["comments"]), "tasks": len(data["tasks"]), "bookmarks": len(data["bookmarks"]),
                  "chat_contacts": len(data.get("chat_contacts") or [])}
        man = {"format": FORMAT, "saga": VERSION, "schema": db.SCHEMA_VERSION, "account": uid, "created": int(data["created"]),
               "created_utc": datetime.fromtimestamp(data["created"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "counts": counts, "options": data["options"], "files": z.sums}
        z.z.writestr("manifest.json", json.dumps(man, ensure_ascii=False, indent=1))
    finally:
        z.close()
    return counts


# =====================================================================================================
# Ein Lauf
# =====================================================================================================
def _ukey(uid):
    return binascii.crc32(uid.encode()) & 0x7FFFFFFF


def running(uid):
    with psycopg.connect(db.DATABASE_URL, autocommit=True) as c:
        got = c.execute("SELECT pg_try_advisory_lock(%s, %s)", (LOCK_ID, _ukey(uid))).fetchone()[0]
        if got:
            c.execute("SELECT pg_advisory_unlock(%s, %s)", (LOCK_ID, _ukey(uid)))
        return not got


def _step(rid, code, **p):
    try:
        with _db() as c, c:
            c.execute("UPDATE user_backup_run SET step=? WHERE id=?", (json.dumps({"code": code, "p": p}), rid))
    except Exception:  # noqa: BLE001 - nur Anzeige
        pass


def _do_run(uid, cfg, pw, rid, now):
    adm = admin_cfg()
    if not adm["enabled"]:
        raise BackupError("disabled")
    if not has_target(cfg):
        raise BackupError("notarget")
    if not _target_ok(cfg):
        raise BackupError("notallowed")
    recips = backups.recipients(cfg)
    if not recips:
        raise BackupError("nokey")
    limit = adm["max_mb"] * MB
    name = "saga-" + backups._stamp(now) + ".zip.age"
    work = os.path.join(tmp_root(), f"{hashlib.sha256(uid.encode()).hexdigest()[:12]}-{uuid.uuid4().hex[:8]}")
    os.makedirs(work, mode=0o700)
    dav = dav_for(cfg, pw)
    last = [0.0]

    def prog(code, force=False, **p):
        if force or time.time() - last[0] > 2:
            last[0] = time.time()
            _step(rid, code, **p)
    try:
        dav.ensure_root()                                 # Ziel frueh pruefen
        with _db() as c, c:
            c.execute("UPDATE user_backup_run SET name=? WHERE id=?", (name, rid))
        prog("step.collect", True)
        zpath = os.path.join(work, "backup.zip")
        tok = i18n.set_lang(account_lang(uid))           # LIESMICH/Markdown in der Sprache des Kontos (auch im Zeitplan)
        try:
            counts = build_zip(uid, cfg, zpath, limit, name)
        finally:
            i18n._cur.reset(tok)
        prog("step.encrypt", True)
        apath = os.path.join(work, name)
        backups._encrypt_file(zpath, apath, recips)
        os.remove(zpath)
        size, sha = os.path.getsize(apath), backups.sha256_file(apath)
        if size > limit + MB:
            raise BackupError("toolarge", size=size, max=limit)
        free = dav.free_space()
        if free is not None and free < size + size // 50 + MB:
            raise BackupError("full", need=size, free=free)
        dav.put_file(apath, dav.url(name), lambda s, n: prog("step.upload", name=name, pct=int(100 * s / max(1, n))))
        backups._verify(dav, dav.url(name), name, {"size": size, "sha256": sha}, False, prog)
        side = f"{sha}  {name}\n".encode()
        dav.req("PUT", dav.url(name + ".sha256"), "PUT", ok=(200, 201, 204), content=side,
                headers={"Content-Type": "text/plain; charset=utf-8"})   # zuletzt: erst jetzt gilt das Backup als fertig
        prog("step.prune", True)
        pruned = prune(dav, int(cfg.get("keep") or 7), keep={name}, now=now)
        return {"name": name, "size": size, "sha256": sha, "counts": counts, "pruned": len(pruned), "chunked": dav.chunked,
                "recipients": [backups.fingerprint(r) for r in recips]}
    finally:
        dav.close()
        shutil.rmtree(work, ignore_errors=True)


def run(uid, trigger="manuell", slot=None, now=None):
    """Ein Lauf fuer uid -> (ok, Ergebnis | BackupError | None)."""
    lc = psycopg.connect(db.DATABASE_URL, autocommit=True)
    try:
        if not lc.execute("SELECT pg_try_advisory_lock(%s, %s)", (LOCK_ID, _ukey(uid))).fetchone()[0]:
            return False, BackupError("busy")
        cfg, pw, st = load(uid)
        if slot is not None and float(st.get("last_attempt") or 0) >= slot:
            return False, None
        now = now or time.time()
        set_state(uid, last_attempt=now)
        with _db() as c, c:
            c.execute("UPDATE user_backup_run SET finished=?, error=? WHERE uid=? AND finished IS NULL",
                      (now, BackupError("aborted").as_json(), uid))
            rid = c.execute("INSERT INTO user_backup_run (uid, started, trigger) VALUES (?,?,?)", (uid, now, trigger)).lastrowid
            c.execute("DELETE FROM user_backup_run WHERE uid=? AND id NOT IN (SELECT id FROM user_backup_run WHERE uid=? "
                      "ORDER BY id DESC LIMIT ?)", (uid, uid, HISTORY_KEEP))
        try:
            res = _do_run(uid, cfg, pw, rid, now)
            with _db() as c, c:
                c.execute("UPDATE user_backup_run SET finished=?, ok=1, size=?, detail=?, step='' WHERE id=?",
                          (time.time(), res["size"], json.dumps(res), rid))
            set_state(uid, last_ok=time.time(), last_name=res["name"])
            print(f"userbackup: Backup fuer ein Konto gesichert ({backups.size_text(res['size'])}, {trigger})", flush=True)
            return True, res
        except Exception as ex:  # noqa: BLE001 - jeder Fehler wird gemeldet, Saga laeuft weiter
            err = ex if isinstance(ex, BackupError) else BackupError("other", detail=type(ex).__name__)
            if not isinstance(ex, BackupError):
                print(f"userbackup: {type(ex).__name__}: {ex}", flush=True)
            with _db() as c, c:
                c.execute("UPDATE user_backup_run SET finished=?, ok=0, error=?, step='' WHERE id=?", (time.time(), err.as_json(), rid))
                notify.create(c, uid, KIND, None, data={"code": "failed", "p": {"code": err.code, "p": err.p}})
            print(f"userbackup: fehlgeschlagen ({trigger}): {err.code}", flush=True)
            return False, err
    finally:
        try:
            lc.execute("SELECT pg_advisory_unlock(%s, %s)", (LOCK_ID, _ukey(uid)))
        except Exception:  # noqa: BLE001
            pass
        lc.close()


def list_backups(dav):
    """Backups im Ziel (neueste zuerst): {name, time, size, complete}. Andere Dateien im Ordner bleiben unberuehrt."""
    ents = dav.listdir(dav.root)
    names = {e["name"] for e in ents}
    out = []
    for e in ents:
        m = NAME_RE.match(e["name"])
        if e["dir"] or not m:
            continue
        rt = backups.run_time("saga-" + m.group(1) + "-" + m.group(2))
        out.append({"name": e["name"], "time": rt.timestamp() if rt else 0, "size": e.get("size"),
                    "complete": e["name"] + ".sha256" in names})
    return sorted(out, key=lambda x: x["name"], reverse=True)


def prune(dav, keep_n, keep=(), now=None):
    """Die neuesten keep_n vollstaendigen Backups bleiben; unvollstaendige nach einem Tag weg."""
    now = now or time.time()
    items = list_backups(dav)
    stay = {x["name"] for x in [i for i in items if i["complete"]][:max(1, keep_n)]} | set(keep)
    gone = []
    for x in items:
        if x["name"] in stay or (not x["complete"] and now - x["time"] < 86400):
            continue
        dav.delete(dav.url(x["name"]))
        dav.delete(dav.url(x["name"] + ".sha256"))
        gone.append(x["name"])
    return gone


# =====================================================================================================
# Zeitplan
# =====================================================================================================
def eff_schedule(cfg, adm=None):
    adm = adm or admin_cfg()
    s = cfg.get("schedule") if cfg.get("schedule") in SCHEDULES else "off"
    return "weekly" if s == "daily" and adm["max_schedule"] == "weekly" else s


def slot_for(cfg, now, adm=None):
    s = eff_schedule(cfg, adm)
    slot = backups.slot_for({"time": cfg.get("time") or "02:40"}, now)
    if s == "weekly":
        wd = int(cfg.get("weekday") if cfg.get("weekday") is not None else 6) % 7
        while datetime.fromtimestamp(slot, TZ).weekday() != wd:
            slot = backups.slot_for({"time": cfg.get("time") or "02:40"}, slot - 3600)
    return slot


def due(cfg, st, now, adm=None):
    if eff_schedule(cfg, adm) == "off" or not has_target(cfg) or not backups.recipients(cfg):
        return None
    slot = slot_for(cfg, now, adm)
    if slot <= float(cfg.get("since") or 0) or float(st.get("last_attempt") or 0) >= slot:
        return None
    return slot


def next_run(cfg, st, now=None):
    now = now or time.time()
    if eff_schedule(cfg) == "off" or not has_target(cfg) or not backups.recipients(cfg):
        return None
    if due(cfg, st, now):
        return now
    nxt = slot_for(cfg, now)
    step = 7 if eff_schedule(cfg) == "weekly" else 1
    while nxt <= now or nxt <= float(cfg.get("since") or 0):
        nxt = slot_for(cfg, nxt + step * 86400 + 3600)
    return nxt


_worker = {"thread": None, "stop": False}
_wake = threading.Event()


def tick(now=None):
    """Eine Runde des Zeitplans (Tests rufen sie direkt) -> Anzahl gestarteter Laeufe."""
    adm = admin_cfg()
    if not adm["enabled"]:
        return 0
    now = now or time.time()
    lc = psycopg.connect(db.DATABASE_URL, autocommit=True)
    n = 0
    try:
        if not lc.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_ID,)).fetchone()[0]:
            return 0                                  # ein anderer Prozess arbeitet den Zeitplan gerade ab
        with _db() as c:
            uids = [r[0] for r in c.execute("SELECT b.uid FROM user_backup b JOIN users u ON u.username=b.uid "
                                            "WHERE u.disabled=0 ORDER BY b.uid")]
        for uid in uids:
            if _worker["stop"]:
                break
            cfg, _, st = load(uid)
            slot = due(cfg, st, now, adm)
            if slot:
                backups._low_priority()
                run(uid, "zeitplan", slot=slot, now=now)
                n += 1
    finally:
        try:
            lc.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
        except Exception:  # noqa: BLE001
            pass
        lc.close()
    return n


def _loop():
    _wake.wait(min(45.0, backups.TICK))
    while not _worker["stop"]:
        try:
            tick()
        except Exception as ex:  # noqa: BLE001
            print(f"userbackup: {type(ex).__name__}: {ex}", flush=True)
        _wake.wait(backups.TICK)
        _wake.clear()


def start():
    if RESTORE_TEST:
        return
    shutil.rmtree(tmp_root(), ignore_errors=True)     # Reste abgebrochener Laeufe/Uploads
    if _worker["thread"] is None:
        _worker["stop"] = False
        th = threading.Thread(target=_loop, name="userbackup", daemon=True)
        _worker["thread"] = th
        th.start()


def stop():
    _worker["stop"] = True
    _wake.set()
    _worker["thread"] = None


# =====================================================================================================
# Wiederherstellen (Import ins eigene Konto)
# =====================================================================================================
class ImportError_(ValueError):
    pass


class KeyError_(ImportError_):
    """Entschluesseln fehlgeschlagen (falscher/fehlender Schluessel): der Upload bleibt fuer einen neuen Versuch."""


def decrypt_to(src, dst, key):
    """age -d mit dem privaten Schluessel ueber stdin (nie auf der Platte) -> dst."""
    key = str(key or "").strip()
    if "AGE-SECRET-KEY-" not in key:
        raise KeyError_(t("Bitte den privaten Schlüssel (AGE-SECRET-KEY-…) angeben."))
    lines = [ln.strip() for ln in key.splitlines() if ln.strip().startswith("AGE-SECRET-KEY-")]
    r = subprocess.run(["age", "-d", "-i", "-", "-o", dst, src], input=("\n".join(lines) + "\n").encode(),
                       capture_output=True, timeout=3600)
    if r.returncode:
        raise KeyError_(t("Entschlüsseln fehlgeschlagen – passt der Schlüssel zu diesem Backup?"))
    return dst


def is_age(path):
    with open(path, "rb") as f:
        return f.read(22) == b"age-encryption.org/v1\n"


def _is_age_safe(path):
    try:
        return is_age(path)
    except OSError:
        return False


def _read_member(z, name, limit, want=None):
    info = z.getinfo(name)
    if info.file_size > limit:
        raise ImportError_(t("Backup zu groß."))
    h, buf = hashlib.sha256(), bytearray()
    with z.open(info) as f:
        for b in iter(lambda: f.read(MB), b""):
            buf.extend(b)
            h.update(b)
            if len(buf) > limit:
                raise ImportError_(t("Backup zu groß."))
    if want and (want.get("sha256") != h.hexdigest() or want.get("size") != len(buf)):
        raise ImportError_(t("Prüfsumme stimmt nicht: {name}", name=name))
    return bytes(buf)


_NOTE_REF = re.compile(r"/notes#([0-9]{1,18})")


def _remap_text(s, nmap, fmap):
    if not isinstance(s, str) or not s:
        return s
    s = _NOTE_REF.sub(lambda m: "/notes#" + str(nmap.get(int(m.group(1)), m.group(1))), s)
    return _FILE_URL.sub(lambda m: "/files/" + fmap.get(m.group(1), m.group(1)), s)


def _remap_blocks(obj, nmap):
    """Seiten-IDs in Block-Daten: eingebettete Datenbank (db), Schaltflaechen (target, tpl)."""
    if isinstance(obj, list):
        for x in obj:
            _remap_blocks(x, nmap)
    elif isinstance(obj, dict):
        d = obj.get("data") if isinstance(obj.get("data"), dict) else None
        if d is not None and obj.get("type") == "database" and isinstance(d.get("db"), int):
            d["db"] = nmap.get(d["db"], d["db"])
        if d is not None and obj.get("type") == "buttons" and isinstance(d.get("buttons"), list):
            for b in d["buttons"]:
                if isinstance(b, dict):
                    for k in ("target", "tpl"):
                        if isinstance(b.get(k), int):
                            b[k] = nmap.get(b[k], b[k])
        for v in obj.values():
            if isinstance(v, (dict, list)):
                _remap_blocks(v, nmap)


def _own_files_only(blocks, own):
    """Wie beim Speichern (notes._check_file_refs): Bild-/Datei-Bloecke nur mit EIGENEN hochgeladenen Dateien. Sonst (fremde
    Datei, fremde Adresse aus einem manipulierten Backup, Datei nicht im Backup) -> Absatz mit dem Dateinamen."""
    from html import escape
    from saga.notes import _iter_blocks
    for b in _iter_blocks(blocks):
        if b.get("type") not in ("image", "attaches"):
            continue
        d = b.get("data") if isinstance(b.get("data"), dict) else {}
        f = d.get("file") if isinstance(d.get("file"), dict) else {}
        m = _FILE_URL.fullmatch(str(f.get("url") or ""))
        if m and own(m.group(1)):
            continue
        label = next((str(x) for x in (f.get("name"), d.get("title"), d.get("caption")) if isinstance(x, str) and x.strip()), "")
        b["type"], b["data"] = "paragraph", {"text": escape(re.sub(r"<[^>]*>", "", label), quote=False)[:300]}


def _clean_body(body, fmt, nmap, fmap, own=lambda fid: False):
    from saga.pages import clean_blocks
    body = _remap_text(str(body or ""), nmap, fmap)
    if fmt != "blocks":
        return body[:NOTE_BODY_MAX], "text"            # Textseite: Grenze wie beim Speichern (pages.py)
    try:
        doc = json.loads(body) if body else {"blocks": []}
        _remap_blocks(doc.get("blocks") if isinstance(doc, dict) else [], nmap)
        out = json.loads(clean_blocks(json.dumps(doc, ensure_ascii=False))[0])
        _own_files_only(out["blocks"], own)
        return clean_blocks(json.dumps(out, ensure_ascii=False))[0], "blocks"
    except (ValueError, TypeError, AttributeError):
        return json.dumps({"blocks": []}), "blocks"


def import_zip(uid, zpath, opts):
    """ZIP (entschluesselt) ins Konto uid einspielen -> Zusammenfassung. Alles in EINER Transaktion; Dateien vorher."""
    from saga.pages import _icon, _tags, _title_in
    adm = admin_cfg()
    limit = adm["max_mb"] * MB
    try:
        z = zipfile.ZipFile(zpath)
    except (zipfile.BadZipFile, OSError):
        raise ImportError_(t("Keine gültige Backup-Datei."))
    with z:
        if len(z.infolist()) > IMPORT_FILES_MAX + 4 * NOTE_TOTAL_MAX + 100:      # Dateien + Markdown je Seite + Rahmen
            raise ImportError_(t("Backup enthält zu viele Einträge."))
        names = set(z.namelist())
        if "manifest.json" not in names or "daten.json" not in names:
            raise ImportError_(t("Keine gültige Backup-Datei (manifest.json/daten.json fehlen)."))
        man = _json_load(_read_member(z, "manifest.json", 8 * MB).decode("utf-8", "replace"), {})
        if not isinstance(man, dict) or man.get("format") != FORMAT:
            raise ImportError_(t("Unbekanntes Backup-Format."))
        sums = man.get("files") if isinstance(man.get("files"), dict) else {}
        data = _json_load(_read_member(z, "daten.json", min(limit, DATA_MAX), sums.get("daten.json")).decode("utf-8", "replace"), {})
        if not isinstance(data, dict) or data.get("format") != FORMAT or not isinstance(data.get("notes"), list):
            raise ImportError_(t("Unbekanntes Backup-Format."))
        notes = [n for n in data["notes"] if isinstance(n, dict) and isinstance(n.get("id"), int)]
        if not opts.get("trash"):
            gone = {n["id"] for n in notes if n.get("deleted_at")}
            notes = [n for n in notes if n["id"] not in gone]
        by_id = {n["id"]: n for n in notes}
        files = [f for f in (data.get("files") or []) if isinstance(f, dict) and re.fullmatch(r"[0-9a-f]{24}", str(f.get("id")))
                 and f.get("path") in names and f.get("path") in sums]
        if len(files) > IMPORT_FILES_MAX:
            raise ImportError_(t("Backup enthält zu viele Dateien (höchstens {n}).", n=IMPORT_FILES_MAX))
        need = sum(int(sums[f["path"]].get("size") or 0) for f in files)
        with _db() as c:
            have = c.execute("SELECT COUNT(*) FROM note WHERE uid=?", (uid,)).fetchone()[0]
            used = c.execute("SELECT COALESCE(SUM(size),0) FROM file WHERE uid=?", (uid,)).fetchone()[0]
        if have + len(notes) + 1 > NOTE_TOTAL_MAX:
            raise ImportError_(t("Zu viele Seiten für dieses Konto (höchstens {n}).", n=NOTE_TOTAL_MAX))
        if used + need > FILE_QUOTA:
            raise ImportError_(t("Speicherplatz voll (max. {n} MB)", n=FILE_QUOTA // 1048576))
        # 1) Dateien: neue IDs, Pruefsumme je Datei
        fmap, written = {}, []
        try:
            for f in files:
                info = z.getinfo(f["path"])
                if info.file_size != sums[f["path"]].get("size"):
                    raise ImportError_(t("Prüfsumme stimmt nicht: {name}", name=f["path"]))
                nid = os.urandom(12).hex()
                dst = os.path.join(FILES_DIR, nid)
                h = hashlib.sha256()
                with z.open(info) as src, open(dst, "wb") as out:
                    written.append(dst)
                    for b in iter(lambda: src.read(MB), b""):
                        h.update(b)
                        out.write(b)
                if h.hexdigest() != sums[f["path"]].get("sha256"):
                    raise ImportError_(t("Prüfsumme stimmt nicht: {name}", name=f["path"]))
                fmap[f["id"]] = (nid, f)
            return _import_db(uid, data, notes, by_id, fmap, opts, _icon, _tags, _title_in)
        except BaseException:
            for p in written:
                try:
                    os.remove(p)
                except OSError:
                    pass
            raise


def _import_db(uid, data, notes, by_id, fmap, opts, _icon, _tags, _title_in):
    from saga.files import _sniff_image
    now = time.time()
    fids = {old: new for old, (new, _) in fmap.items()}
    with _db() as c, c:
        own_cache = {}

        def own(fid):
            if fid not in own_cache:
                own_cache[fid] = bool(c.execute("SELECT 1 FROM file WHERE id=? AND uid=?", (fid, uid)).fetchone())
            return own_cache[fid]
        for old, (new, f) in fmap.items():
            with open(os.path.join(FILES_DIR, new), "rb") as fh:
                img = _sniff_image(fh.read(16))
            c.execute("INSERT INTO file (id, uid, name, mime, size, is_image, created) VALUES (?,?,?,?,?,?,?)",
                      (new, uid, re.sub(r"[\x00-\x1f/\\]", "_", str(f.get("name") or "datei"))[:120] or "datei",
                       img or "application/octet-stream", os.path.getsize(os.path.join(FILES_DIR, new)), 1 if img else 0, now))
        folder = None
        if opts.get("folder", True):
            day = i18n.fmt_date(float(data.get("created") or now), TZ)
            from saga.pages import clean_blocks
            body = clean_blocks(json.dumps({"blocks": [{"id": "ub1", "type": "paragraph", "data": {
                "text": t("Aus dem persönlichen Backup vom {date} eingespielt.", date=day)}}]}))[0]
            folder = c.execute("INSERT INTO note (uid, title, body, format, icon, created, updated, sort, created_by, updated_by) "
                               "VALUES (?,?,?,?,?,?,?,?,?,?)", (uid, t("Wiederhergestellt {date}", date=day), body, "blocks", "♻️",
                                                                now, now, now, uid, uid)).lastrowid
        # 2) neue IDs vorab vergeben (Verweise zwischen den Seiten zeigen sofort auf die neuen Seiten)
        nmap = {}
        for n in notes:
            nmap[n["id"]] = c.execute("SELECT nextval(pg_get_serial_sequence('note', 'id'))").fetchone()[0]

        def depth(n, seen=()):
            p = n.get("parent_id")
            return 0 if p not in by_id or p in seen else 1 + depth(by_id[p], seen + (n["id"],))
        order = sorted(notes, key=lambda n: (depth(n), n["id"]))
        schemas, made, skipped = {}, [], 0
        for n in order:
            try:
                nid = nmap[n["id"]]
                par = nmap.get(n.get("parent_id")) if n.get("parent_id") in by_id else folder
                kind = "database" if n.get("kind") == "database" else "page"
                body, fmt = _clean_body(n.get("body"), n.get("format"), nmap, fids, own)
                body = nextcloud.adopt(body, fmt, uid, nid)     # Nextcloud-Bloecke: eigene neu signieren, fremde nur als Karte
                schema = None
                if kind == "database":
                    raw = _json_load(n.get("schema"), {"props": []})
                    raw = raw if isinstance(raw, dict) else {"props": []}
                    props = []
                    for p in raw.get("props") or []:
                        if isinstance(p, dict) and p.get("type") == "relation":
                            tg = int(p["target"]) if str(p.get("target", "")).isdigit() else None
                            if tg not in nmap:
                                continue              # Ziel-Datenbank nicht im Backup: Spalte weglassen
                            p = dict(p, target=nmap[tg])
                        props.append(p)
                    sc = _clean_schema(dict(raw, props=props))
                    schemas[nid] = sc
                    schema = json.dumps(sc, ensure_ascii=False)
                pv = None
                if par in schemas:                    # Zeile einer (eingespielten) Datenbank
                    raw = _json_load(n.get("props"), {})
                    raw = raw if isinstance(raw, dict) else {}
                    rel = {p["id"] for p in schemas[par]["props"] if p["type"] == "relation"}
                    raw = {k: ([nmap[x] for x in v if isinstance(x, int) and x in nmap] if k in rel and isinstance(v, list) else v)
                           for k, v in raw.items()}
                    raw = _files_in(schemas[par], _people_in(c, uid, schemas[par], raw), fids, own)   # 2.5: Personen/Dateien
                    clean = {}
                    for k, v in raw.items():
                        try:
                            clean.update(_clean_props(schemas[par], {k: v}, trusted=True))
                        except (ValueError, TypeError):
                            pass
                    pv = json.dumps(clean, ensure_ascii=False) if clean else None
                icon = _remap_text(str(n.get("icon") or ""), nmap, fids)
                try:
                    icon = _icon(icon, c, uid) if icon else None
                except ValueError:
                    icon = None
                cover = _remap_text(str(n.get("cover") or ""), nmap, fids)
                if not (cover and _FILE_URL.fullmatch(cover) and c.execute("SELECT 1 FROM file WHERE id=? AND uid=? AND is_image=1",
                                                                           (cover[7:], uid)).fetchone()):
                    cover = None
                try:
                    tags = json.dumps(_tags(_json_load(n.get("tags"), []) or []), ensure_ascii=False) if n.get("tags") else None
                except ValueError:
                    tags = None
                num = lambda v, d=None: float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else d   # noqa: E731
                c.execute("INSERT INTO note (id, uid, title, body, format, icon, parent_id, sort, tags, kind, schema, props, cover, "
                          "cover_pos, wide, font, small, pinned, is_template, archived, locked, deleted_at, created, updated, created_by, updated_by) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (nid, uid, _title_in(n.get("title") or ""), body, fmt, icon, par, num(n.get("sort"), now), tags, kind, schema,
                           pv, cover, num(n.get("cover_pos")) if cover else None, 1 if n.get("wide") else 0,
                           n.get("font") if n.get("font") in ("serif", "mono") else None, 1 if n.get("small") else 0, 1 if n.get("pinned") else 0,
                           1 if n.get("is_template") else 0, num(n.get("archived")), 1 if n.get("locked") else 0,
                           now if n.get("deleted_at") else None, num(n.get("created"), now), num(n.get("updated"), now), uid, uid))
                made.append(nid)
            except (ValueError, TypeError, KeyError) as ex:
                skipped += 1
                print(f"userbackup: Seite beim Einspielen uebersprungen ({type(ex).__name__})", flush=True)
                nmap.pop(n["id"], None)
        versions = 0
        if opts.get("versions", True):
            per, made_set = {}, set(made)
            vs = [v for v in data.get("versions") or [] if isinstance(v, dict) and v.get("note_id") in nmap
                  and nmap[v["note_id"]] in made_set]
            vs.sort(key=lambda v: -(v["created"] if isinstance(v.get("created"), (int, float)) else 0))
            for v in vs:                              # je Seite hoechstens VERSION_MAX (die neuesten), wie im Verlauf
                per[v["note_id"]] = per.get(v["note_id"], 0) + 1
                if per[v["note_id"]] > VERSION_MAX:
                    continue
                body, fmt = _clean_body(v.get("body"), v.get("format"), nmap, fids, own)
                c.execute("INSERT INTO note_version (note_id, uid, title, body, format, created, author) VALUES (?,?,?,?,?,?,?)",
                          (nmap[v["note_id"]], uid, str(v.get("title") or "")[:300], body, fmt,
                           float(v["created"]) if isinstance(v.get("created"), (int, float)) else now, uid))
                versions += 1
        tasks = bms = 0
        if opts.get("tasks"):
            for x in data.get("tasks") or []:
                if isinstance(x, dict) and str(x.get("text") or "").strip():
                    c.execute("INSERT INTO task (uid, text, done, due, created, done_at, prio, repeat, rep_anchor) VALUES (?,?,?,?,?,?,?,?,?)",
                              (uid, str(x["text"])[:2000], 1 if x.get("done") else 0, str(x["due"])[:20] if x.get("due") else None,
                               now, None, int(x.get("prio") or 2) if str(x.get("prio") or "2").isdigit() else 2,
                               str(x["repeat"])[:40] if x.get("repeat") else None, str(x["rep_anchor"])[:20] if x.get("rep_anchor") else None))
                    tasks += 1
        srcs = 0
        if opts.get("tasks"):
            srcs = _import_task_sources(c, uid, data, nmap, set(made))
        if opts.get("bookmarks"):
            for x in data.get("bookmarks") or []:
                u = str((x or {}).get("url") or "") if isinstance(x, dict) else ""
                if urlparse(u).scheme in ("http", "https") and len(u) <= 2000:
                    c.execute("INSERT INTO bookmark (uid, name, url, icon, category, sort, created) VALUES (?,?,?,?,?,?,?)",
                              (uid, str(x.get("name") or u)[:200], u, "", str(x.get("category") or "Lesezeichen")[:60],
                               float(x["sort"]) if isinstance(x.get("sort"), (int, float)) else now, now))
                    bms += 1
        ncon = 0
        if opts.get("contacts"):                       # 2.5: Kontakte (lokale nur, mit denen ein Chat erlaubt ist)
            ncon = contacts.restore(c, uid, data.get("chat_contacts"),
                                    may_local=lambda ref: chat.may_contact(c, uid, contacts.local_uid(c, ref) or "~"))
        if folder or made:
            tree_changed(c, uid, ([folder] if folder else []) + made, "created")
    return {"pages": len(made), "files": len(fmap), "versions": versions, "tasks": tasks, "task_sources": srcs, "bookmarks": bms,
            "contacts": ncon, "skipped": skipped, "folder": folder}


def _import_task_sources(c, uid, data, nmap, made):
    """Aufgabenquellen (2.5) und taegliche Zusammenfassung einspielen. Eigene Datenbanken aus dem Backup -> neue IDs; uebrige
    IDs nur, wenn das Konto Zugriff hat und die Datenbank eine Zuordnung hat (tasksrc.subscribe prueft) - sonst verworfen.
    Bestehende Abos und eine schon gesetzte Zusammenfassung bleiben unveraendert."""
    from saga import tasksrc
    n = 0
    for x in data.get("task_sources") or []:
        if not isinstance(x, dict) or isinstance(x.get("db"), bool) or not isinstance(x.get("db"), int):
            continue
        old = x["db"]
        if old in nmap:
            db_id = nmap[old]
            if db_id not in made:
                continue                              # Datenbank wurde nicht angelegt
        elif x.get("own") or old <= 0:
            continue                              # eigene Datenbank nicht im Backup (ausgeschlossen/Papierkorb) bzw. fremde Instanz
        else:
            db_id = old                           # geteilte Datenbank eines anderen Kontos: Zugriff prueft subscribe
        if c.execute("SELECT 1 FROM task_source WHERE uid=? AND db_id=?", (uid, db_id)).fetchone():
            continue
        scope = x.get("scope") if x.get("scope") in tasksrc.SCOPES else None
        got, err = tasksrc.subscribe(c, uid, db_id, scope)
        if err is not None and scope is not None:
            got, err = tasksrc.subscribe(c, uid, db_id, None)    # z. B. 'assigned' ohne Personen-Eigenschaft
        if err is None:
            n += 1
    raw = (data.get("settings") or {}).get("task_due") if isinstance(data.get("settings"), dict) else None
    cfg = _json_load(raw, {}) if isinstance(raw, str) else None
    if isinstance(cfg, dict) and not c.execute("SELECT 1 FROM user_kv WHERE uid=? AND k='task_due'", (uid,)).fetchone():
        from saga.push import TIME_RE
        out = dict(tasksrc.DIGEST_DEF)
        if isinstance(cfg.get("on"), bool):
            out["on"] = cfg["on"]
        if isinstance(cfg.get("time"), str) and TIME_RE.fullmatch(cfg["time"]):
            out["time"] = cfg["time"]
        if isinstance(cfg.get("tz"), str) and cfg["tz"] in tasksrc._zones():
            out["tz"] = cfg["tz"]
        c.execute("INSERT OR IGNORE INTO user_kv (uid, k, v) VALUES (?, 'task_due', ?)", (uid, json.dumps(out)))
    return n


# =====================================================================================================
# Login Flow v2 je Konto
# =====================================================================================================
_flows = {}                       # uid -> {state, login, until, code, p, id}
_flow_lock = threading.Lock()


def flow_state(uid):
    with _flow_lock:
        f = dict(_flows.get(uid) or {"state": ""})
    if f.get("state") == "wait" and time.time() > f.get("until", 0):
        f.update(state="err", code="flow_expired")
    return f


def flow_cancel(uid):
    with _flow_lock:
        _flows.pop(uid, None)


def flow_start(uid, server, folder):
    host = urlparse(server).hostname

    def go():
        with backups._client_for(host, timeout=20.0, ip_ok=ip_ok) as cl:
            return cl.post(server + "/index.php/login/v2", headers={"Accept": "application/json"})
    r = backups._net_call(go)
    if r.status_code != 200:
        raise BackupError("http", status=r.status_code, what="login/v2")
    try:
        j = r.json()
        login, token, endpoint = str(j["login"]), j["poll"]["token"], j["poll"]["endpoint"]
    except (ValueError, KeyError, TypeError):
        raise BackupError("http", status=r.status_code, what="login/v2")
    if (urlparse(endpoint).hostname or "").lower() != (host or "").lower() or urlparse(endpoint).scheme != urlparse(server).scheme:
        endpoint = server + "/login/v2/poll"
    if urlparse(login).scheme not in ("https", "http") or (urlparse(login).hostname or "").lower() != (host or "").lower():
        raise BackupError("http", status=r.status_code, what="login/v2")
    fid = uuid.uuid4().hex
    with _flow_lock:
        _flows[uid] = {"state": "wait", "login": login, "until": time.time() + 20 * 60, "code": "", "p": {}, "id": fid}
    threading.Thread(target=_flow_poll, args=(uid, fid, server, endpoint, token, folder), name="userbackup-login", daemon=True).start()
    return login


def _flow_set(uid, fid, **kw):
    with _flow_lock:
        if (_flows.get(uid) or {}).get("id") == fid:
            _flows[uid].update(kw)


def _flow_poll(uid, fid, server, endpoint, token, folder):
    host = urlparse(server).hostname
    while True:
        with _flow_lock:
            f = _flows.get(uid)
            if not f or f["id"] != fid:
                return
            if time.time() > f["until"]:
                f.update(state="err", code="flow_expired")
                return
        time.sleep(2)
        try:
            def go():
                with backups._client_for(host, timeout=15.0, ip_ok=ip_ok) as cl:
                    return cl.post(endpoint, data={"token": token}, headers={"Accept": "application/json"})
            r = backups._net_call(go)
            if r.status_code == 404:
                continue
            if r.status_code != 200:
                raise BackupError("http", status=r.status_code, what="login/v2/poll")
            j = r.json()
            login, app_pw = str(j["loginName"]), str(j["appPassword"])
            dav_user = backups.nc_user_id(server, login, app_pw, ip_ok=ip_ok)
            cfg, old_pw, _ = load(uid)
            if cfg.get("flow") and old_pw and cfg.get("server"):
                backups.nc_revoke(cfg["server"], cfg.get("user") or "", old_pw, ip_ok=ip_ok)
            cfg.update(mode="nextcloud", server=server, url=server, user=login, dav_user=dav_user, flow=True,
                       folder=folder or cfg.get("folder") or DEFAULT["folder"])
            save(uid, cfg, app_pw)
            try:
                backups.test_target(dict(cfg, password=app_pw), ip_ok=ip_ok)
                _flow_set(uid, fid, state="ok", code="")
            except BackupError as ex:
                _flow_set(uid, fid, state="ok", code="test:" + ex.code, p=ex.p)
            return
        except BackupError as ex:
            _flow_set(uid, fid, state="err", code=ex.code, p=ex.p)
            return
        except (ValueError, KeyError, TypeError):
            _flow_set(uid, fid, state="err", code="http", p={"status": 200, "what": "login/v2/poll"})
            return


# =====================================================================================================
# HTTP-Schnittstelle (Einstellungen -> "Mein Backup"; auch fuer Apps, siehe docs/APP-API.md)
# =====================================================================================================
_keys = {}                        # uid -> {pub, last6, until}: neu erzeugter Schluessel bis zur Bestaetigung (ohne privaten Teil)
_uploads = {}                     # token -> {uid, path, until, size}
_mem_lock = threading.Lock()


def _guard(request, need_enabled=True, outbound=False):
    """outbound: der Aufruf verbindet sich mit dem Ziel - im Testwiederherstellungs-Modus (SAGA_RESTORE_TEST=1) gesperrt."""
    uid = _api_guard(request)
    if not uid:
        return None, _bad("unauthorized", 401)
    if need_enabled and not admin_cfg()["enabled"]:
        return None, _bad(text("disabled"), 403)
    if outbound and RESTORE_TEST:
        return None, _bad(t("Testwiederherstellung (SAGA_RESTORE_TEST=1): keine Backups."), 409)
    return uid, None


def _runs(uid, limit=10):
    with _db() as c:
        rows = c.execute("SELECT started, finished, trigger, name, ok, size, step, error FROM user_backup_run WHERE uid=? "
                         "ORDER BY id DESC LIMIT ?", (uid, limit)).fetchall()
    return [{"started": r[0], "finished": r[1], "trigger": r[2], "name": r[3], "ok": bool(r[4]), "size": r[5],
             "step": text_json(r[6]) if r[6] else "", "error": text_json(r[7]) if r[7] else ""} for r in rows]


def _pages(uid):
    """Eigene Seiten fuer die Ausschluss-Auswahl (ohne Datenbank-Zeilen, ohne Papierkorb)."""
    with _db() as c:
        rows = c.execute("SELECT n.id, n.title, n.icon, n.parent_id, n.kind FROM note n LEFT JOIN note p ON p.id=n.parent_id AND p.uid=n.uid "
                         "WHERE n.uid=? AND n.deleted_at IS NULL AND (p.id IS NULL OR p.kind<>'database') ORDER BY n.sort, n.id LIMIT 3000",
                         (uid,)).fetchall()
    return [{"id": r[0], "title": r[1] or "", "icon": r[2] if r[2] and not str(r[2]).startswith("/files/") else "", "parent": r[3],
             "db": r[4] == "database"} for r in rows]


def status(uid):
    cfg, pw, st = load(uid)
    adm = admin_cfg()
    fl = flow_state(uid)
    code = fl.get("code") or ""
    msg = (t("Anmeldung abgelaufen – bitte neu verbinden.") if code == "flow_expired"
           else text(code.split(":", 1)[-1], fl.get("p")) if code else "")
    if fl.get("state") in ("ok", "err"):
        flow_cancel(uid)                              # Ergebnis nur einmal zeigen
    runs = _runs(uid)
    with _mem_lock:
        pend = _keys.get(uid)
        pend = pend if pend and pend["until"] > time.time() else None
    return {"allowed": adm["enabled"], "age_ok": backups.age_ok(),
            "limits": {"max_mb": adm["max_mb"], "min_h": adm["min_h"], "max_schedule": adm["max_schedule"]},
            "target": ({"kind": cfg["mode"], "address": cfg["server"] if cfg["mode"] == "nextcloud" else cfg["url"],
                        "user": cfg.get("user") or "", "folder": cfg.get("folder") or "", "flow": bool(cfg.get("flow")),
                        "allowed": _target_ok(cfg)} if has_target(cfg) else None),
            "flow": {"state": fl.get("state") or "", "login": fl.get("login") if fl.get("state") == "wait" else None, "message": msg},
            "keys": [{"short": backups.short_key(r), "fp": backups.fingerprint(r)} for r in backups.recipients(cfg)],
            "pending_key": {"public": pend["pub"]} if pend else None,
            "schedule": cfg["schedule"], "effective_schedule": eff_schedule(cfg, adm), "time": cfg["time"], "weekday": cfg["weekday"],
            "keep": cfg["keep"], "cats": cfg["cats"], "exclude": cfg["exclude"],
            "running": bool(runs and runs[0]["finished"] is None and running(uid)),
            "last_ok": float(st.get("last_ok") or 0) or None, "last_attempt": float(st.get("last_attempt") or 0) or None,
            "next": next_run(cfg, st) if adm["enabled"] else None, "runs": runs,
            "ready": has_target(cfg) and bool(backups.recipients(cfg)) and backups.age_ok()}


@router.get("/api/account/backup")
def api_status(request: Request, pages: int = 0):
    uid, err = _guard(request, need_enabled=False)
    if err:
        return err
    out = status(uid)
    if pages:
        out["pages"] = _pages(uid)
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


@router.post("/api/account/backup/connect")
async def api_connect(request: Request):
    """{url, user?, password?, folder?}: Nextcloud ohne Benutzer -> Login Flow v2 ({flow: login-url}), sonst pruefen + speichern."""
    import asyncio
    uid, err = _guard(request, outbound=True)
    if err:
        return err
    b = await _body(request) or {}
    url, msg = norm_target_url(b.get("url"))
    if not url:
        return _bad(msg)
    folder = str(b.get("folder") or "").strip().strip("/") or DEFAULT["folder"]
    if ".." in folder.split("/") or not re.fullmatch(r"[^\x00-\x1f\\]{1,200}", folder):
        return _bad(t("Ordnername ungültig."))
    user, pw = str(b.get("user") or "").strip()[:200], str(b.get("password") or "")[:500]

    def work():
        cfg, old_pw, _ = load(uid)
        info = backups.detect(url, ip_ok=ip_ok)
        if info["nextcloud"] and not norm_target_url(info["server"])[0]:
            raise BackupError("blocked")
        if info["nextcloud"] and not user:
            return {"flow": flow_start(uid, info["server"], folder)}
        new = dict(cfg, folder=folder, flow=False, user=user)
        if info["nextcloud"]:
            new.update(mode="nextcloud", server=info["server"], url=info["server"],
                       dav_user=backups.nc_user_id(info["server"], user, pw, ip_ok=ip_ok))
        else:
            new.update(mode="webdav", server="", url=url, dav_user="")
        free = backups.test_target(dict(new, password=pw), ip_ok=ip_ok)
        if cfg.get("flow") and old_pw and cfg.get("server"):
            backups.nc_revoke(cfg["server"], cfg.get("user") or "", old_pw, ip_ok=ip_ok)
        save(uid, new, pw)
        return {"ok": True, "kind": new["mode"], "free": free}
    try:
        return JSONResponse(await asyncio.to_thread(work))
    except BackupError as ex:
        return _bad(text(ex.code, ex.p), 400 if ex.code in ("blocked", "auth") else 502)


@router.post("/api/account/backup/flow/cancel")
def api_flow_cancel(request: Request):
    uid, err = _guard(request, need_enabled=False)
    if err:
        return err
    flow_cancel(uid)
    return JSONResponse({"ok": True})


@router.post("/api/account/backup/test")
def api_test(request: Request):
    uid, err = _guard(request, outbound=True)
    if err:
        return err
    cfg, pw, _ = load(uid)
    if not has_target(cfg):
        return _bad(text("notarget"), 409)
    if not _target_ok(cfg):
        return _bad(text("notallowed"), 409)
    try:
        free = backups.test_target(dict(cfg, password=pw), ip_ok=ip_ok)
    except BackupError as ex:
        return _bad(text(ex.code, ex.p), 502)
    return JSONResponse({"ok": True, "free": free})


@router.delete("/api/account/backup/target")
def api_disconnect(request: Request):
    uid, err = _guard(request, need_enabled=False, outbound=True)
    if err:
        return err
    cfg, pw, _ = load(uid)
    revoked = None
    if cfg.get("flow") and pw and cfg.get("server"):
        revoked = backups.nc_revoke(cfg["server"], cfg.get("user") or "", pw, ip_ok=ip_ok)
    cfg.update(mode="", server="", url="", user="", dav_user="", flow=False)
    save(uid, cfg, "")
    flow_cancel(uid)
    return JSONResponse({"ok": True, "revoked": revoked})


@router.post("/api/account/backup/key/generate")
def api_keygen(request: Request):
    """Neues Schluesselpaar: der private Schluessel steht NUR in dieser Antwort (Saga speichert ihn nicht)."""
    uid, err = _guard(request)
    if err:
        return err
    if not backups.age_ok():
        return _bad(t("age fehlt auf dem Server."), 503)
    priv, pub = backups.keygen()
    last = priv.strip().splitlines()[-1].strip()[-6:].upper()
    with _mem_lock:
        _keys[uid] = {"pub": pub, "last6": hashlib.sha256(last.encode()).hexdigest(), "until": time.time() + KEY_TTL}
    host = re.sub(r"[^a-z0-9.-]+", "-", (urlparse(BASE_URL).hostname or "saga").lower())[:60]
    return JSONResponse({"private": priv, "public": pub, "filename": f"saga-backup-{host}-{uid}.key"},
                        headers={"Cache-Control": "no-store"})


@router.post("/api/account/backup/key/confirm")
async def api_key_confirm(request: Request):
    """{last6}: letzte 6 Zeichen des privaten Schluessels -> oeffentlichen Schluessel eintragen."""
    uid, err = _guard(request)
    if err:
        return err
    b = await _body(request) or {}
    with _mem_lock:
        p = _keys.get(uid)
        if not p or p["until"] < time.time():
            _keys.pop(uid, None)
            return _bad(t("Abgelaufen – bitte neu erzeugen."), 410)
        if b.get("drop"):
            _keys.pop(uid, None)
            return JSONResponse({"ok": True})
        if hashlib.sha256(str(b.get("last6") or "").strip().upper().encode()).hexdigest() != p["last6"]:
            return _bad(t("Die letzten 6 Zeichen stimmen nicht – bitte den privaten Schlüssel noch einmal prüfen."))
        _keys.pop(uid, None)
    cfg, _, _ = load(uid)
    rs = backups.recipients(cfg)
    if p["pub"] not in rs:
        rs.append(p["pub"])
    cfg["recipients"] = rs[-5:]
    save(uid, cfg)
    return JSONResponse({"ok": True, "fp": backups.fingerprint(p["pub"])})


@router.post("/api/account/backup/key")
async def api_key_add(request: Request):
    """{key}: eigenen oeffentlichen Schluessel eintragen (age1... oder ssh-ed25519 ...)."""
    import asyncio
    uid, err = _guard(request)
    if err:
        return err
    b = await _body(request) or {}
    raw = str(b.get("key") or "")[:2000]
    if "AGE-SECRET-KEY" in raw.upper():
        return _bad(t("Das ist ein privater Schlüssel – hier gehört nur der öffentliche hin (age1…)."))
    k = await asyncio.to_thread(backups.norm_recipient, raw)
    if not k:
        return _bad(t("Kein gültiger öffentlicher Schlüssel (age1… oder ssh-ed25519 …). Private Schlüssel (AGE-SECRET-KEY-…) gehören nicht hierher."))
    cfg, _, _ = load(uid)
    rs = backups.recipients(cfg)
    if k not in rs:
        if len(rs) >= 5:
            return _bad(t("Höchstens {n} Schlüssel.", n=5))
        rs.append(k)
    cfg["recipients"] = rs
    save(uid, cfg)
    return JSONResponse({"ok": True, "fp": backups.fingerprint(k)})


@router.delete("/api/account/backup/key/{fp}")
def api_key_del(fp: str, request: Request):
    uid, err = _guard(request, need_enabled=False)
    if err:
        return err
    cfg, _, _ = load(uid)
    cfg["recipients"] = [r for r in backups.recipients(cfg) if backups.fingerprint(r) != fp]
    save(uid, cfg)
    return JSONResponse({"ok": True})


@router.put("/api/account/backup/settings")
async def api_settings(request: Request):
    """{schedule: off|weekly|daily, time: hh:mm, weekday: 0-6, keep: 1-60, cats: {...}, exclude: [seiten-id]} (alles optional)."""
    uid, err = _guard(request)
    if err:
        return err
    b = await _body(request)
    if not isinstance(b, dict):
        return _bad(t("ungültige Angabe"))
    cfg, _, _ = load(uid)
    if "schedule" in b:
        if b["schedule"] not in SCHEDULES:
            return _bad(t("ungültige Angabe"))
        if b["schedule"] == "daily" and admin_cfg()["max_schedule"] == "weekly":
            return _bad(t("Auf diesem Server höchstens wöchentlich."))
        if b["schedule"] != "off" and cfg.get("schedule") == "off":
            cfg["since"] = time.time()
        cfg["schedule"] = b["schedule"]
    if "time" in b:
        if not TIME_RE.match(str(b["time"] or "")):
            return _bad(t("Uhrzeit ungültig (hh:mm)."))
        cfg["time"] = str(b["time"])
    if "weekday" in b:
        try:
            cfg["weekday"] = int(b["weekday"]) % 7
        except (TypeError, ValueError):
            return _bad(t("ungültige Angabe"))
    if "keep" in b:
        try:
            cfg["keep"] = max(1, min(60, int(b["keep"])))
        except (TypeError, ValueError):
            return _bad(t("ungültige Angabe"))
    if isinstance(b.get("cats"), dict):
        cfg["cats"].update({k: bool(v) for k, v in b["cats"].items() if k in CATS})
    if "exclude" in b:
        if not isinstance(b["exclude"], list):
            return _bad(t("ungültige Angabe"))
        ids = [int(x) for x in b["exclude"] if (isinstance(x, int) and not isinstance(x, bool)) or str(x).isdigit()][:500]
        with _db() as c:                              # nur eigene Seiten
            own = {r[0] for k in range(0, len(ids), 500) for r in c.execute(
                f"SELECT id FROM note WHERE uid=? AND id IN ({','.join('?' * len(ids[k:k + 500]))})", (uid, *ids[k:k + 500]))} if ids else set()
        cfg["exclude"] = [i for i in dict.fromkeys(ids) if i in own]
    save(uid, cfg)
    return JSONResponse(status(uid))


@router.post("/api/account/backup/run")
def api_run(request: Request):
    """Jetzt sichern (laeuft im Hintergrund; Status ueber GET /api/account/backup)."""
    uid, err = _guard(request)
    if err:
        return err
    if RESTORE_TEST:
        return _bad(t("Testwiederherstellung (SAGA_RESTORE_TEST=1): keine Backups."), 409)
    cfg, _, st = load(uid)
    if not (has_target(cfg) and backups.recipients(cfg)):
        return _bad(text("notarget" if not has_target(cfg) else "nokey"), 409)
    adm = admin_cfg()
    last = float(st.get("last_attempt") or 0)
    if adm["min_h"] and time.time() - last < adm["min_h"] * 3600:
        return JSONResponse({"error": t("Zu häufig – das nächste Backup ist ab {when} möglich.",
                                        when=i18n.fmt_date(last + adm["min_h"] * 3600, TZ, with_time=True)),
                             "retry_at": last + adm["min_h"] * 3600}, status_code=429)
    if running(uid):
        return _bad(text("busy"), 409)
    threading.Thread(target=run, args=(uid, "manuell"), name="userbackup-run", daemon=True).start()
    return JSONResponse({"started": True}, status_code=202)


@router.get("/api/account/backup/remote")
def api_remote(request: Request):
    uid, err = _guard(request, outbound=True)
    if err:
        return err
    cfg, pw, _ = load(uid)
    if not has_target(cfg):
        return JSONResponse({"items": []})
    if not _target_ok(cfg):
        return _bad(text("notallowed"), 409)
    dav = dav_for(cfg, pw)
    try:
        return JSONResponse({"items": list_backups(dav)}, headers={"Cache-Control": "no-store"})
    except BackupError as ex:
        return _bad(text(ex.code, ex.p), 502)
    finally:
        dav.close()


@router.get("/api/account/backup/remote/{name}")
def api_remote_get(name: str, request: Request):
    """Verschluesseltes Backup aus dem eigenen Ziel herunterladen (durchgereicht)."""
    uid, err = _guard(request, outbound=True)
    if err:
        return err
    if not NAME_RE.match(name):
        return _bad("not found", 404)
    cfg, pw, _ = load(uid)
    if not has_target(cfg) or not _target_ok(cfg):
        return _bad("not found", 404)
    dav = dav_for(cfg, pw)
    e = None
    try:
        e = dav.stat(dav.url(name))
    except BackupError as ex:
        dav.close()
        return _bad(text(ex.code, ex.p), 502)
    if not e:
        dav.close()
        return _bad("not found", 404)
    import queue
    q = queue.Queue(maxsize=8)

    def pump():
        try:                                          # Gegenstelle bricht ab -> nach 2 Minuten ohne Abnehmer Schluss
            dav.get_to(dav.url(name), lambda b: q.put(b, timeout=120))
        except Exception:  # noqa: BLE001 - Abbruch: Download endet unvollstaendig
            pass
        finally:
            try:
                q.put(None, timeout=5)
            except queue.Full:
                pass
            dav.close()
    threading.Thread(target=pump, daemon=True).start()

    def gen():
        while True:
            b = q.get()
            if b is None:
                return
            yield b
    headers = {"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"}
    if e.get("size") is not None:
        headers["Content-Length"] = str(e["size"])
    return StreamingResponse(gen(), media_type="application/octet-stream", headers=headers)


def _upload_dir():
    d = os.path.join(tmp_root(), "uploads")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def _drop_upload(tok):
    with _mem_lock:
        u = _uploads.pop(tok, None)
        for k in [k for k, v in _uploads.items() if v["until"] < time.time()]:
            v = _uploads.pop(k)
            try:
                os.remove(v["path"])
            except OSError:
                pass
    if u:
        try:
            os.remove(u["path"])
        except OSError:
            pass


@router.post("/api/account/backup/restore/upload")
async def api_restore_upload(request: Request):
    """Backup-Datei (Rohdaten, .zip.age oder entschluesseltes .zip) hochladen -> {token}. Fuer das Einspielen danach."""
    uid, err = _guard(request)
    if err:
        return err
    limit = admin_cfg()["max_mb"] * MB + MB
    if int(request.headers.get("content-length") or 0) > limit:
        return _bad(t("Backup zu groß."), 413)
    tok = secrets.token_urlsafe(18)
    path = os.path.join(_upload_dir(), tok)
    n = 0
    try:
        with open(path, "wb") as f:
            async for chunk in request.stream():
                n += len(chunk)
                if n > limit:
                    raise ImportError_("big")
                f.write(chunk)
    except ImportError_:
        os.remove(path)
        return _bad(t("Backup zu groß."), 413)
    if n < 22:
        os.remove(path)
        return _bad(t("Keine gültige Backup-Datei."))
    enc = is_age(path)
    if not enc and not zipfile.is_zipfile(path):
        os.remove(path)
        return _bad(t("Keine gültige Backup-Datei."))
    _drop_upload(None)                                # abgelaufene aufraeumen
    with _mem_lock:                                   # hoechstens ein wartender Upload je Konto (sonst Platte voll)
        prev = [k for k, v in _uploads.items() if v["uid"] == uid]
        _uploads[tok] = {"uid": uid, "path": path, "until": time.time() + UPLOAD_TTL, "size": n}
    for k in prev:
        _drop_upload(k)
    return JSONResponse({"token": tok, "size": n, "encrypted": enc})


_restore_busy = set()


@router.post("/api/account/backup/restore")
async def api_restore(request: Request):
    """{token | remote, key?, folder=true, trash=false, versions=true, tasks=false, bookmarks=false} -> Zusammenfassung.
    Spielt NUR ins eigene Konto ein. key = privater age-Schluessel (nur fuer diesen Vorgang, nie gespeichert)."""
    import asyncio
    uid, err = _guard(request)
    if err:
        return err
    b = await _body(request) or {}
    tok, remote = str(b.get("token") or ""), str(b.get("remote") or "")
    opts = {"folder": b.get("folder", True) is not False, "trash": b.get("trash") is True, "versions": b.get("versions", True) is not False,
            "tasks": b.get("tasks") is True, "bookmarks": b.get("bookmarks") is True, "contacts": b.get("contacts") is True}
    key = str(b.get("key") or "")[:4000]
    with _mem_lock:
        up = _uploads.get(tok) if tok else None
        if tok and (not up or up["uid"] != uid):
            return _bad("not found", 404)             # fremde oder abgelaufene Uploads gibt es nicht
        if uid in _restore_busy:
            return _bad(t("Es wird gerade schon ein Backup eingespielt."), 409)
        _restore_busy.add(uid)

    def work():
        wd = os.path.join(tmp_root(), "restore-" + uuid.uuid4().hex[:12])
        os.makedirs(wd, mode=0o700)
        try:
            if up:
                src = up["path"]
            elif NAME_RE.match(remote):
                if RESTORE_TEST:
                    raise ImportError_(t("Testwiederherstellung (SAGA_RESTORE_TEST=1): keine Backups."))
                cfg, pw, _ = load(uid)
                if not has_target(cfg) or not _target_ok(cfg):
                    raise ImportError_(text("notarget"))
                dav = dav_for(cfg, pw)
                try:
                    side = dav.get_bytes(dav.url(remote + ".sha256"), 4096).decode("ascii", "replace").split()
                    src = os.path.join(wd, remote)
                    with open(src, "wb") as f:
                        lim, got = admin_cfg()["max_mb"] * MB + MB, [0]

                        def sink(x):
                            got[0] += len(x)
                            if got[0] > lim:
                                raise ImportError_(t("Backup zu groß."))
                            f.write(x)
                        dav.get_to(dav.url(remote), sink)
                finally:
                    dav.close()
                if not side or backups.sha256_file(src) != side[0]:
                    raise ImportError_(t("Prüfsumme stimmt nicht: {name}", name=remote))
            else:
                raise ImportError_(t("Bitte eine Backup-Datei wählen."))
            zp = src
            if is_age(src):
                zp = decrypt_to(src, os.path.join(wd, "backup.zip"), key)
            return import_zip(uid, zp, opts)
        finally:
            shutil.rmtree(wd, ignore_errors=True)
    keep = False
    try:
        res = await asyncio.to_thread(work)
    except KeyError_ as ex:
        keep = bool(up and _is_age_safe(up["path"]))        # verschluesselt: mit anderem Schluessel erneut
        return _bad(str(ex))
    except ImportError_ as ex:
        return _bad(str(ex))
    except BackupError as ex:
        return _bad(text(ex.code, ex.p), 502)
    finally:
        with _mem_lock:
            _restore_busy.discard(uid)
        if up and not keep:
            _drop_upload(tok)                        # Erfolg oder Fehler beim Einspielen: Datei (ggf. Klartext-ZIP) sofort weg
    print(f"userbackup: Backup eingespielt ({res['pages']} Seiten, {res['files']} Dateien)", flush=True)
    return JSONResponse(res)
